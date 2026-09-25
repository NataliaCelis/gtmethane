"""
concat.py -- Picarro monthly concatenation.

Based on the user's existing zip-concatenation script: unzip a monthly
archive (yyyy-mm.zip), find every .dat, keep only
EPOCH_TIME / CO2_dry / CH4_dry / H2O, concatenate in chronological order.

Extended here to also accept:
  * a folder tree of .dat files (any nesting depth)
  * an already-concatenated CSV  (EPOCH_TIME,CO2_dry,CH4_dry,H2O)
  * the DATE_TIME_UTC / EPOCH_MIN export format
so the same loader serves the Streamlit app and the CLI.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd

KEEP_COLS = ["EPOCH_TIME", "CO2_dry", "CH4_dry", "H2O"]
DATA_CODES = [-888, -999]


def _read_dat(fileobj, name=""):
    """One whitespace-delimited Picarro .dat -> DataFrame with KEEP_COLS.

    Handles the '_sync' column variant seen in some months
    (CO2_dry_sync etc.) by stripping the suffix.
    """
    try:
        df = pd.read_csv(fileobj, sep=r"\s+", engine="c")
    except Exception as exc:
        return None, f"{name}: unreadable ({exc})"

    df.columns = [c.strip() for c in df.columns]
    ren = {c: c[:-5] for c in df.columns
           if c.endswith("_sync") and c[:-5] not in df.columns}
    if ren:
        df = df.rename(columns=ren)

    missing = [c for c in KEEP_COLS if c not in df.columns]
    if missing:
        return None, f"{name}: missing {missing}"
    return df[KEEP_COLS], None


def concat_zip(zip_bytes_or_path, progress=None):
    """The user's original routine: read every .dat inside the archive."""
    zf = zipfile.ZipFile(
        io.BytesIO(zip_bytes_or_path) if isinstance(zip_bytes_or_path, bytes)
        else zip_bytes_or_path)

    names = sorted(n for n in zf.namelist()
                   if n.endswith(".dat")
                   and not Path(n).name.startswith("._")
                   and not n.startswith("__MACOSX"))
    if not names:
        raise ValueError("No .dat files found inside the archive.")

    frames, warnings = [], []
    for i, n in enumerate(names):
        with zf.open(n) as fh:
            df, warn = _read_dat(fh, n)
        if warn:
            warnings.append(warn)
        elif df is not None and not df.empty:
            frames.append(df)
        if progress:
            progress((i + 1) / len(names), f"{i+1}/{len(names)}  {Path(n).name}")

    if not frames:
        raise ValueError("No data could be read from the .dat files.")
    return _finalize(pd.concat(frames, ignore_index=True)), len(names), warnings


def concat_folder(folder, progress=None):
    """Same, for an unzipped tree. Recursive, so odd nesting is fine."""
    folder = Path(folder)
    names = sorted(p for p in folder.rglob("*.dat")
                   if not p.name.startswith("._"))
    if not names:
        raise ValueError(f"No .dat files found under {folder}")

    frames, warnings = [], []
    for i, p in enumerate(names):
        df, warn = _read_dat(p, p.name)
        if warn:
            warnings.append(warn)
        elif df is not None and not df.empty:
            frames.append(df)
        if progress:
            progress((i + 1) / len(names), f"{i+1}/{len(names)}  {p.name}")

    if not frames:
        raise ValueError("No data could be read from the .dat files.")
    return _finalize(pd.concat(frames, ignore_index=True)), len(names), warnings


def read_concat_csv(fileobj_or_path):
    """An already-concatenated CSV, in either known layout."""
    df = pd.read_csv(fileobj_or_path)
    df.columns = [c.strip() for c in df.columns]

    if "EPOCH_MIN" in df.columns:            # DATE_TIME_UTC export
        df = df.rename(columns={"EPOCH_MIN": "EPOCH_TIME"})
    if "EPOCH_TIME" not in df.columns:
        raise ValueError(f"No EPOCH_TIME / EPOCH_MIN column. Found: {list(df.columns)}")

    missing = [c for c in KEEP_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing column(s): {missing}")
    return _finalize(df[KEEP_COLS])


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    df["EPOCH_TIME"] = pd.to_numeric(df["EPOCH_TIME"], errors="coerce")
    df = df.dropna(subset=["EPOCH_TIME"])
    df = df.sort_values("EPOCH_TIME", kind="mergesort").reset_index(drop=True)
    for c in ("CO2_dry", "CH4_dry", "H2O"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df.loc[df[c].isin(DATA_CODES), c] = pd.NA   # data codes are not readings
    return df


def to_indexed(df: pd.DataFrame) -> pd.DataFrame:
    """EPOCH_TIME column -> UTC DatetimeIndex (still the raw Picarro clock)."""
    out = df.copy()
    out.index = pd.to_datetime(out["EPOCH_TIME"], unit="s", utc=True)
    out = out.drop(columns=["EPOCH_TIME"])
    return out[~out.index.duplicated(keep="first")].sort_index()


# ------------------------------------------------------------- weather

def read_weather(fileobj_or_path):
    """Weather CSV: Record ID, Timestamp(unix), ... Dewpoint, Barometer,
    Solar Radiation Sensor. Tolerates CR-only line endings."""
    if hasattr(fileobj_or_path, "read"):
        raw = fileobj_or_path.read()
        raw = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    else:
        raw = Path(fileobj_or_path).read_bytes().decode("utf-8", "replace")
    if "\r" in raw and "\n" not in raw:
        raw = raw.replace("\r", "\n")

    df = pd.read_csv(io.StringIO(raw))
    df.columns = [c.strip() for c in df.columns]
    if "Timestamp" not in df.columns:
        raise ValueError(f"Weather file has no Timestamp column. Found: {list(df.columns)}")

    ren = {}
    for c in df.columns:
        lc = c.lower()
        if lc == "dewpoint":
            ren[c] = "dewpoint"
        elif lc == "barometer":
            ren[c] = "barometer"
        elif "solar" in lc:
            ren[c] = "solar_radiation"
    df = df.rename(columns=ren)

    df.index = pd.to_datetime(df["Timestamp"], unit="s", utc=True)
    keep = [c for c in ("dewpoint", "barometer", "solar_radiation") if c in df.columns]
    if "dewpoint" not in keep:
        raise ValueError("Weather file has no Dewpoint column (needed for Clausius-Clapeyron).")
    out = df[keep].sort_index()
    # duplicate timestamps are common in station exports and break the
    # reindex/interpolate used to put weather on the Picarro time base
    return out[~out.index.duplicated(keep="first")]

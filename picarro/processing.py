"""
processing.py -- exact ports of the science functions in ProcessPicarro.ipf
(Trisha Tambe, March 9, 2026), for the Picarro G2301 (Instrument 1).

Every constant and formula here is copied from the Igor procedure. Where
Igor and Python differ only in mechanics (Igor's epoch vs unix epoch), the
result is identical; those spots are commented.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- constants

MISSING_DATA_CODE = -888.0     # Igor: ReplaceNaNs() writes -888
AVG_SECONDS = 600              # Igor: XSec = 600  (10-minute averages)
MIN_NUM_NON_NANS = 1           # Igor: minNumNonNans = 1

# Igor ESTProcessPicarroTime(): picarro_time -= 3600*8
# Igor EDTProcessPicarroTime(): picarro_time -= 3600*7
PICARRO_OFFSET_HOURS = {"EST": -8, "EDT": -7}

# Igor ConvertToPercentH2O() -- Clausius-Clapeyron constants, verbatim
CC_E0 = 6.1078        # reference saturation vapor pressure (hPa)
CC_T0 = 273.16        # reference temp for e0, triple point of H2O (K)
CC_LV = 2.501e6       # latent heat of vaporization (J/kg)
CC_RV = 461.5         # gas constant for water vapor (J/(kg K))
INHG_TO_HPA = 33.8639 # Igor: pressure_hPa *= 33.8639

# Used only when the weather file has no barometer column. Igor always
# expects one; this is a documented fallback, not part of the port.
DEFAULT_PRESSURE_HPA = 975.0

GAS_COLS = ["CO2_dry", "CH4_dry", "H2O"]


# ------------------------------------------------------------ time handling

def apply_picarro_offset(index: pd.DatetimeIndex, timezone: str) -> pd.DatetimeIndex:
    """Igor ProcessPicarroTime / ESTProcessPicarroTime / EDTProcessPicarroTime.

    Igor adds date2secs(1970,1,1) to convert unix -> Igor epoch, then
    subtracts 8 h (EST) or 7 h (EDT). Python already works in unix epoch,
    so only the timezone shift is needed; the resulting wall-clock time is
    the same as Igor's.
    """
    if timezone not in PICARRO_OFFSET_HOURS:
        raise ValueError(f"timezone must be EST or EDT, got {timezone!r}")
    return index + pd.Timedelta(hours=PICARRO_OFFSET_HOURS[timezone])


def utc_to_est(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Igor utc_to_est(): edittimezone -= 60*60*5"""
    return index - pd.Timedelta(hours=5)


def utc_to_edt(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Igor utc_to_edt(): edittimezone -= 60*60*4"""
    return index - pd.Timedelta(hours=4)


# ------------------------------------------------- Clausius-Clapeyron

def convert_to_percent_h2o(dewpoint_f, barometer_inhg=None):
    """Igor ConvertToPercentH2O(), line for line.

        dewpoint_K  = (dewpoint_F - 32) * (5/9) + 273.15
        pressure_hPa = barometer_inHg * 33.8639
        e_actual    = e0 * exp((Lv/Rv) * (1/T0 - 1/dewpoint_K))
        wstation_H2O = (e_actual / pressure_hPa) * 100

    barometer_inhg=None -> DEFAULT_PRESSURE_HPA is used instead.
    """
    dewpoint_k = (pd.Series(dewpoint_f).astype(float) - 32.0) * (5.0 / 9.0) + 273.15

    if barometer_inhg is None:
        pressure_hpa = pd.Series(DEFAULT_PRESSURE_HPA, index=dewpoint_k.index)
    else:
        pressure_hpa = pd.Series(barometer_inhg).astype(float) * INHG_TO_HPA

    e_actual = CC_E0 * np.exp((CC_LV / CC_RV) * (1.0 / CC_T0 - 1.0 / dewpoint_k))
    return (e_actual / pressure_hpa) * 100.0


def co2_wet_to_dry(co2, h2o):
    """Igor CO2wet_to_dry():  CO2_dry = CO2 / (1 - H2O/100)

    Instrument 2 only. Included for completeness; Instrument 1 files
    already carry CO2_dry.
    """
    return pd.Series(co2).astype(float) / (1.0 - pd.Series(h2o).astype(float) / 100.0)


# ------------------------------------------------------- measure keys

def new_measure_key(index) -> pd.Series:
    """Igor EditCH4/EditCO2/EditH2O: Make/D/O/N=(...) meas_key; meas_key = 1"""
    return pd.Series(1, index=index, dtype="int8")


def zeros_between(key: pd.Series, t0, t1) -> pd.Series:
    """Igor GeneralMacros Make_zeros_between_cursors (cursors A and B)."""
    key = key.copy()
    key.loc[t0:t1] = 0
    return key


def ones_between(key: pd.Series, t0, t1) -> pd.Series:
    """Igor GeneralMacros Make_ones_between_cursors -- restores data."""
    key = key.copy()
    key.loc[t0:t1] = 1
    return key


def apply_key(original: pd.Series, key: pd.Series) -> pd.Series:
    """Igor dependency:  x_edit := (meas_key == 1) ? x_original : NaN"""
    return original.where(key == 1, np.nan)


def sync_measure_keys(key_ch4: pd.Series, key_co2: pd.Series,
                      key_h2o: pd.Series):
    """Igor SyncMeasureKeys(): a point survives only if all three keys are 1.

        keep = (snap_ch4 == 1 && snap_CO2 == 1 && snap_H2O == 1) ? 1 : 0
    """
    keep = ((key_ch4 == 1) & (key_co2 == 1) & (key_h2o == 1)).astype("int8")
    return keep.copy(), keep.copy(), keep.copy()


# ---------------------------------------------------------- averaging

def averag_seconds(df: pd.DataFrame, cols=None, xsec: int = AVG_SECONDS,
                   min_non_nans: int = MIN_NUM_NON_NANS) -> pd.DataFrame:
    """Igor AveragCH4/AveragCO2/AveragH2O via
    DoAveragOnlyStartStopMinNonNans, with XSec = 600.

    Bin edges follow Igor exactly:
        startTime = t[0]  - mod(t[0],  XSec)
        stopTime  = t[-1] - mod(t[-1], XSec) + XSec
    A bin with fewer than min_non_nans real points becomes NaN.
    """
    cols = cols or [c for c in GAS_COLS if c in df.columns]
    if df.empty:
        return pd.DataFrame(columns=["start_UTC", "stop_UTC"] + cols)

    t0 = df.index[0].timestamp()
    t1 = df.index[-1].timestamp()
    start = t0 - (t0 % xsec)
    stop = t1 - (t1 % xsec) + xsec

    edges = pd.to_datetime(np.arange(start, stop + xsec, xsec), unit="s", utc=True)
    g = df[cols].groupby(pd.cut(df.index, bins=edges, right=False), observed=False)
    means = g.mean()
    counts = g.count()
    means[counts < min_non_nans] = np.nan

    out = pd.DataFrame(index=range(len(edges) - 1))
    out["start_UTC"] = edges[:-1]
    out["stop_UTC"] = edges[1:]
    for c in cols:
        out[c] = means[c].to_numpy()
    return out


def replace_nans(series: pd.Series) -> pd.Series:
    """Igor ReplaceNaNs():  w = (numtype(w[p]) == 2) ? -888 : w[p]"""
    return series.fillna(MISSING_DATA_CODE)


def final_table(edited: pd.DataFrame) -> pd.DataFrame:
    """Igor FinalTable(): start/stop UTC + CO2/CH4/H2O 10-min averages,
    NaN replaced with the Missing Data Code."""
    avg = averag_seconds(edited)
    tbl = pd.DataFrame()
    tbl["Start Average (UTC)"] = avg["start_UTC"]
    tbl["Stop Average (UTC)"] = avg["stop_UTC"]
    tbl["Start_unix"] = (avg["start_UTC"].astype("int64") // 10**9)
    tbl["Stop_unix"] = (avg["stop_UTC"].astype("int64") // 10**9)
    tbl["CO2 (ppm)"] = replace_nans(avg.get("CO2_dry", pd.Series(dtype=float)))
    tbl["CH4 (ppm)"] = replace_nans(avg.get("CH4_dry", pd.Series(dtype=float)))
    tbl["H2O (%)"] = replace_nans(avg.get("H2O", pd.Series(dtype=float)))
    return tbl


# ------------------------------------------------------ diurnal profile

def diurnal(series: pd.Series, index: pd.DatetimeIndex, avg_interval: int = 3600):
    """Igor makeCH4DiurnalWaves + AveragSecs(avgInterval=3600).

    Igor: DiurnalTime = mod(time, 86400), then hourly mean/std.
    Returns (scatter_x_hours, scatter_y, bin_centre_hours, mean, std).
    """
    s = pd.Series(series.to_numpy(), index=index).dropna()
    if s.empty:
        return np.array([]), np.array([]), np.array([]), np.array([]), np.array([])
    seconds = (s.index.hour * 3600 + s.index.minute * 60 + s.index.second).to_numpy()
    bins = seconds // avg_interval
    g = pd.DataFrame({"b": bins, "v": s.to_numpy()}).groupby("b")["v"]
    centres = (g.mean().index.to_numpy() * avg_interval + avg_interval / 2) / 3600.0
    return (seconds / 3600.0, s.to_numpy(), centres,
            g.mean().to_numpy(), g.std().to_numpy())

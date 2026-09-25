"""
Picarro QC -- Streamlit replacement for ProcessPicarro.ipf (Igor Pro).

Run:  streamlit run app.py

Workflow mirrors the Igor menu, one month at a time:
  1  Load        -- zip / folder / CSV  + weather file
  2  Visualize   -- clock correction, Clausius-Clapeyron, diurnal profiles
  3  Edit        -- drag on the plot to select, then keep or remove
  4  Final Table -- 10-minute averages, -888 for gaps, export xlsx/csv
"""
from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from picarro import concat, plotting, processing as proc

st.set_page_config(page_title="Picarro QC", layout="wide",
                   initial_sidebar_state="expanded")

S = st.session_state
S.setdefault("raw", None)          # DataFrame, corrected clock
S.setdefault("weather", None)
S.setdefault("keys", None)         # dict col -> measure key Series
S.setdefault("history", [])        # [(t0, t1, species, action)]
S.setdefault("view", None)         # (lo, hi) current x window
S.setdefault("sel", None)          # (t0, t1) pending selection
S.setdefault("month_label", "")


# ------------------------------------------------------------- helpers

def reset_keys(index):
    S.keys = {c: proc.new_measure_key(index) for c in proc.GAS_COLS}
    S.history = []


def apply_interval(t0, t1, species, action):
    targets = proc.GAS_COLS if species == "all" else [species]
    for c in targets:
        S.keys[c] = (proc.zeros_between(S.keys[c], t0, t1) if action == "remove"
                     else proc.ones_between(S.keys[c], t0, t1))
    S.history.append((t0, t1, species, action))


def removed_spans():
    """Contiguous spans where every species is off -- for plot shading."""
    if S.keys is None:
        return []
    off = (S.keys["CH4_dry"] == 0) & (S.keys["CO2_dry"] == 0) & (S.keys["H2O"] == 0)
    if not off.any():
        return []
    idx = off.index
    d = off.astype(int).diff().fillna(off.iloc[0].astype(int))
    starts = idx[d == 1]
    ends = idx[d == -1]
    if off.iloc[0]:
        starts = starts.insert(0, idx[0])
    if off.iloc[-1]:
        ends = ends.append(pd.DatetimeIndex([idx[-1]]))
    return [(a, b, "all") for a, b in zip(starts, ends)]


def edited_frame():
    df = S.raw
    out = pd.DataFrame(index=df.index)
    for c in proc.GAS_COLS:
        out[c] = proc.apply_key(df[c], S.keys[c])
    return out


# ------------------------------------------------------------- sidebar

st.sidebar.title("Picarro QC")
st.sidebar.caption("Python port of ProcessPicarro.ipf · Instrument 1 (G2301)")

st.sidebar.header("1 · Picarro data")
src = st.sidebar.radio("Source", ["Monthly .zip", "Concatenated .csv", "Folder path"],
                       label_visibility="collapsed")

pic_df = None
if src == "Monthly .zip":
    up = st.sidebar.file_uploader("yyyy-mm.zip", type=["zip"])
    if up and st.sidebar.button("Concatenate", type="primary", width="stretch"):
        bar = st.sidebar.progress(0.0, "reading…")
        try:
            pic_df, n, warns = concat.concat_zip(up.getvalue(),
                                                 progress=lambda f, m: bar.progress(f, m))
            S.month_label = Path(up.name).stem
            st.sidebar.success(f"{len(pic_df):,} rows from {n} .dat files")
            if warns:
                with st.sidebar.expander(f"{len(warns)} file warning(s)"):
                    st.write("\n".join(warns[:50]))
        except Exception as exc:
            st.sidebar.error(str(exc))
        bar.empty()

elif src == "Concatenated .csv":
    up = st.sidebar.file_uploader("EPOCH_TIME,CO2_dry,CH4_dry,H2O", type=["csv"])
    if up and st.sidebar.button("Load", type="primary", width="stretch"):
        try:
            pic_df = concat.read_concat_csv(up)
            S.month_label = Path(up.name).stem
            st.sidebar.success(f"{len(pic_df):,} rows")
        except Exception as exc:
            st.sidebar.error(str(exc))

else:
    path = st.sidebar.text_input("Folder containing .dat files")
    if path and st.sidebar.button("Concatenate", type="primary", width="stretch"):
        bar = st.sidebar.progress(0.0, "reading…")
        try:
            pic_df, n, warns = concat.concat_folder(path,
                                                    progress=lambda f, m: bar.progress(f, m))
            S.month_label = Path(path).name
            st.sidebar.success(f"{len(pic_df):,} rows from {n} .dat files")
        except Exception as exc:
            st.sidebar.error(str(exc))
        bar.empty()

st.sidebar.header("2 · Weather data")
st.sidebar.caption("needs Dewpoint; Barometer and Solar Radiation optional")
w_up = st.sidebar.file_uploader("weather .csv", type=["csv"], key="wx")
if w_up is not None:
    try:
        S.weather = concat.read_weather(w_up)
        st.sidebar.success(f"{len(S.weather):,} rows · {list(S.weather.columns)}")
    except Exception as exc:
        st.sidebar.error(str(exc))

st.sidebar.header("3 · Timezone")
tz = st.sidebar.radio(
    "Picarro clock correction", ["EST", "EDT"], horizontal=True,
    help="Igor: EST −8 h, EDT −7 h. EDT runs from the 2nd Sunday in March "
         "to the 1st Sunday in November; process those two months in two parts.")

# ------------------------------------------------- build the working frame

if pic_df is not None:
    df = concat.to_indexed(pic_df)
    df.index = proc.apply_picarro_offset(df.index, tz)
    S.raw = df
    reset_keys(df.index)
    S.view = (df.index[0], min(df.index[0] + timedelta(days=1), df.index[-1]))
    S.sel = None

if S.raw is None:
    st.title("Picarro QC")
    st.info("Load a month of Picarro data and a weather file from the sidebar to begin.")
    st.markdown("""
**What this does** — the same four steps as the Igor procedure:

| Igor | here |
|---|---|
| `ProcessPicarroTime` (`−3600*7` / `*8`) | timezone selector in the sidebar |
| `ConvertToPercentH2O` | Clausius-Clapeyron, identical constants |
| `EditCH4` / `EditCO2` / `EditH2O` + cursors | drag on the plot → keep / remove |
| `SyncMeasureKeys` | one click, or automatic on export |
| `AveragCH4/CO2/H2O` + `ReplaceNaNs` + `FinalTable` | Final Table tab → xlsx / csv |

Nothing is deleted from the file: removing a stretch sets its measure key to 0,
exactly like Igor, and you can restore it at any time.
""")
    st.stop()

# --------------------------------------------- weather join + C-C conversion

df = S.raw
if S.weather is not None and "wstation_H2O" not in df.columns:
    wx = S.weather[~S.weather.index.duplicated(keep="first")].sort_index()
    w = wx.reindex(df.index.union(wx.index)).interpolate("time").reindex(df.index)
    baro = w["barometer"] if "barometer" in w.columns else None
    station = proc.convert_to_percent_h2o(w["dewpoint"], baro)
    df = df.copy()
    df["wstation_H2O"] = station.to_numpy()
    if "solar_radiation" in w.columns:
        df["solar_radiation"] = w["solar_radiation"].to_numpy()
    S.raw = df

st.title(f"Picarro QC · {S.month_label or 'month'}")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Points", f"{len(df):,}")
c2.metric("Span", f"{df.index[0]:%b %d} → {df.index[-1]:%b %d}")
kept = int((S.keys['CH4_dry'] == 1).sum())
c3.metric("Kept", f"{kept:,}", f"{kept/len(df)*100:.2f} %")
c4.metric("Removed", f"{len(df)-kept:,}")
if S.weather is None:
    st.warning("No weather file loaded — the Clausius-Clapeyron station-H2O "
               "comparison is unavailable until you add one.")

tab_edit, tab_diurnal, tab_h2o, tab_final = st.tabs(
    ["✏️ Edit", "🕐 Diurnal profiles", "💧 H2O comparison", "📋 Final table"])

# ------------------------------------------------------------- EDIT tab

with tab_edit:
    nav, opts = st.columns([3, 2])
    with nav:
        st.markdown("**View window**")
        b = st.columns(6)
        t_start, t_end = df.index[0], df.index[-1]
        lo, hi = S.view
        if b[0].button("Whole month", width="stretch"):
            S.view = (t_start, t_end)
        if b[1].button("7 days", width="stretch"):
            S.view = (lo, min(lo + timedelta(days=7), t_end))
        if b[2].button("1 day", width="stretch"):
            S.view = (lo, min(lo + timedelta(days=1), t_end))
        if b[3].button("6 hours", width="stretch"):
            S.view = (lo, min(lo + timedelta(hours=6), t_end))
        if b[4].button("◀ back", width="stretch"):
            w = S.view[1] - S.view[0]
            S.view = (max(t_start, S.view[0] - w), max(t_start + w, S.view[1] - w))
        if b[5].button("forward ▶", width="stretch"):
            w = S.view[1] - S.view[0]
            S.view = (min(t_end - w, S.view[0] + w), min(t_end, S.view[1] + w))

        d1, d2 = st.columns(2)
        jump = d1.date_input("Jump to date", value=S.view[0].date(),
                             min_value=t_start.date(), max_value=t_end.date())
        if d2.button("Go to that day", width="stretch"):
            j = pd.Timestamp(jump, tz="UTC")
            S.view = (max(t_start, j), min(t_end, j + timedelta(days=1)))

    with opts:
        st.markdown("**Display**")
        show_station = st.checkbox("Overlay station H2O (Clausius-Clapeyron)", True)
        show_solar = st.checkbox("Overlay solar radiation on CH4 (time check)", False)
        height = st.slider("Plot height", 500, 1200, 760, 20)

    lo, hi = S.view
    st.caption(f"Showing **{lo:%Y-%m-%d %H:%M}** → **{hi:%Y-%m-%d %H:%M}**  ·  "
               f"{plotting.point_budget(hi - lo)}  ·  "
               f"drag left-right on any panel to select a time range")

    fig = plotting.qc_figure(df, S.keys, removed_spans(), x_range=(lo, hi),
                             show_station_h2o=show_station, show_solar=show_solar,
                             height=height)
    ev = st.plotly_chart(fig, width="stretch", key="qc",
                         on_select="rerun", selection_mode="box")

    # pull an x-range out of the box selection
    try:
        pts = ev["selection"]["points"]
        if pts:
            xs = pd.to_datetime([p["x"] for p in pts], utc=True)
            S.sel = (xs.min(), xs.max())
    except Exception:
        pass

    st.divider()
    sc1, sc2 = st.columns([2, 3])
    with sc1:
        st.markdown("**Selection**")
        if S.sel:
            t0, t1 = S.sel
            n = int(((df.index >= t0) & (df.index <= t1)).sum())
            st.success(f"{t0:%Y-%m-%d %H:%M:%S} → {t1:%Y-%m-%d %H:%M:%S}\n\n**{n:,} points**")
        else:
            st.info("Drag on the plot, or type a range below.")
        with st.expander("Type an exact range"):
            m1, m2 = st.columns(2)
            a = m1.text_input("from", value=f"{lo:%Y-%m-%d %H:%M:%S}")
            b_ = m2.text_input("to", value=f"{(lo+timedelta(minutes=10)):%Y-%m-%d %H:%M:%S}")
            if st.button("Use this range", width="stretch"):
                try:
                    S.sel = (pd.Timestamp(a, tz="UTC"), pd.Timestamp(b_, tz="UTC"))
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not parse: {exc}")

    with sc2:
        st.markdown("**Apply to selection**")
        species = st.radio("Species", ["all", "CH4_dry", "CO2_dry", "H2O"],
                           horizontal=True,
                           help="Igor lets you edit each graph separately; "
                                "Sync Edits then merges them.")
        a1, a2, a3 = st.columns(3)
        if a1.button("🗑 Remove", type="primary", width="stretch", disabled=S.sel is None):
            apply_interval(*S.sel, species, "remove"); st.rerun()
        if a2.button("↩ Restore", width="stretch", disabled=S.sel is None):
            apply_interval(*S.sel, species, "restore"); st.rerun()
        if a3.button("Clear selection", width="stretch", disabled=S.sel is None):
            S.sel = None; st.rerun()

        u1, u2 = st.columns(2)
        if u1.button("Undo last edit", width="stretch", disabled=not S.history):
            S.history.pop()
            hist = list(S.history)
            reset_keys(df.index)
            for h in hist:
                apply_interval(*h)
            st.rerun()
        if u2.button("Sync edits (all species)", width="stretch"):
            k1, k2, k3 = proc.sync_measure_keys(S.keys["CH4_dry"], S.keys["CO2_dry"], S.keys["H2O"])
            S.keys["CH4_dry"], S.keys["CO2_dry"], S.keys["H2O"] = k1, k2, k3
            st.success("Synced — a point removed in one species is now removed in all.")

    if S.history:
        st.markdown("**Edit history**")
        st.dataframe(pd.DataFrame(
            [{"from": a, "to": b_, "species": s, "action": act}
             for a, b_, s, act in S.history]),
            width="stretch", height=200)

# ---------------------------------------------------------- DIURNAL tab

with tab_diurnal:
    st.caption("Igor MakeCH4DiurnalProfile / MakeCO2DiurnalProfile — "
               "points vs time of day, with hourly mean ± std.")
    ed = edited_frame()
    for col in ("CH4_dry", "CO2_dry"):
        sx, sy, cx, mu, sd = proc.diurnal(ed[col], ed.index)
        st.plotly_chart(plotting.diurnal_figure(sx, sy, cx, mu, sd,
                                                plotting.LABELS[col], plotting.COLORS[col]),
                        width="stretch", key=f"drn_{col}")

# ------------------------------------------------------------- H2O tab

with tab_h2o:
    st.caption("Igor H2OComparison() — Picarro H2O against the dewpoint-derived "
               "station H2O (Clausius-Clapeyron).")
    if "wstation_H2O" not in df.columns:
        st.warning("Load a weather file to enable this comparison.")
    else:
        st.plotly_chart(plotting.h2o_comparison_figure(df, S.keys),
                        width="stretch", key="h2ocmp")
        ed = edited_frame()
        diff = (ed["H2O"] - df["wstation_H2O"]).dropna()
        if len(diff):
            m1, m2, m3 = st.columns(3)
            m1.metric("Mean difference", f"{diff.mean():+.3f} %")
            m2.metric("Std of difference", f"{diff.std():.3f} %")
            m3.metric("Median difference", f"{diff.median():+.3f} %")
            st.caption("A steady offset is usually a calibration difference between "
                       "the two instruments, not bad data. Short excursions are the "
                       "ones worth looking at.")

# ----------------------------------------------------------- FINAL tab

with tab_final:
    st.caption("Igor Step 4 — AveragCH4/CO2/H2O (XSec = 600), ReplaceNaNs → −888, FinalTable.")
    do_sync = st.checkbox("Sync measure keys before averaging (Igor Step 3)", True)

    keys = dict(S.keys)
    if do_sync:
        k1, k2, k3 = proc.sync_measure_keys(keys["CH4_dry"], keys["CO2_dry"], keys["H2O"])
        keys = {"CH4_dry": k1, "CO2_dry": k2, "H2O": k3}
    ed = pd.DataFrame(index=df.index)
    for c in proc.GAS_COLS:
        ed[c] = proc.apply_key(df[c], keys[c])

    tbl = proc.final_table(ed)
    n_missing = int((tbl["CH4 (ppm)"] == proc.MISSING_DATA_CODE).sum())
    f1, f2, f3 = st.columns(3)
    f1.metric("10-minute rows", f"{len(tbl):,}")
    f2.metric("Rows with data", f"{len(tbl)-n_missing:,}")
    f3.metric("Rows = −888", f"{n_missing:,}")

    st.dataframe(tbl.head(400), width="stretch", height=380)

    name = S.month_label or "picarro"
    xbuf = io.BytesIO()
    with pd.ExcelWriter(xbuf, engine="openpyxl") as xl:
        out = tbl.copy()
        for c in ("Start Average (UTC)", "Stop Average (UTC)"):
            out[c] = out[c].dt.tz_localize(None)     # Excel has no tz type
        out.to_excel(xl, sheet_name="Final Table", index=False)
        pd.DataFrame(
            [{"from": a, "to": b_, "species": s, "action": act}
             for a, b_, s, act in S.history]
        ).to_excel(xl, sheet_name="Edit log", index=False)
        pd.DataFrame({
            "setting": ["month", "timezone", "offset_hours", "avg_seconds",
                        "min_non_nans", "missing_code", "points_in", "points_kept"],
            "value": [S.month_label, tz, proc.PICARRO_OFFSET_HOURS[tz],
                      proc.AVG_SECONDS, proc.MIN_NUM_NON_NANS,
                      proc.MISSING_DATA_CODE, len(df), int((keys['CH4_dry'] == 1).sum())],
        }).to_excel(xl, sheet_name="Run info", index=False)

    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇ Excel (.xlsx)", xbuf.getvalue(), f"{name}_final.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary", width="stretch")
    d2.download_button("⬇ Final table (.csv)", tbl.to_csv(index=False).encode(),
                       f"{name}_final.csv", "text/csv", width="stretch")
    simple = pd.DataFrame({"EPOCH_TIME": tbl["Start_unix"],
                           "CO2_dry": tbl["CO2 (ppm)"],
                           "CH4_dry": tbl["CH4 (ppm)"],
                           "H2O": tbl["H2O (%)"]})
    d3.download_button("⬇ Simplified (.csv)", simple.to_csv(index=False).encode(),
                       f"{name}_simplified.csv", "text/csv", width="stretch")

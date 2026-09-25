"""
plotting.py -- Plotly figures for the QC app.

Browser-rendering strategy (the whole point of the decimation here):
a month of 1 Hz Picarro data is ~2.6 M points per trace, which no browser
will draw. So the number of points sent to the browser is capped, and the
cap is chosen by how wide the visible window is:

    <= 1 day   -> every single point (nothing hidden)
    <= 7 days  -> min/max envelope, ~8 k buckets
    > 7 days   -> min/max envelope, ~4 k buckets

The envelope is important: plain stride-sampling (every Nth point) can
step straight over a one-minute dropout and make it invisible. Taking the
min AND max of each bucket guarantees any spike or dropout still shows up
as a vertical excursion, however far you are zoomed out.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

FULL_DETAIL_SPAN = pd.Timedelta("1D")
MEDIUM_SPAN = pd.Timedelta("7D")
MEDIUM_BUCKETS = 8000
COARSE_BUCKETS = 4000

COLORS = {            # Igor's RGB choices, converted
    "CH4_dry": "#66cc01",
    "CO2_dry": "#cc01a4",
    "H2O": "#ad73e6",
    "wstation_H2O": "#40bfff",
    "solar_radiation": "#ffd21e",
    "removed": "#d62728",
}
LABELS = {"CH4_dry": "CH4 (ppm)", "CO2_dry": "CO2 (ppm)", "H2O": "H2O (%)"}


def decimate(index, values, span=None, max_points=None):
    """Min/max envelope decimation. Returns (index, values) unchanged when
    the series already fits under the cap."""
    n = len(index)
    if n == 0:
        return index, values
    if max_points is None:
        if span is None or span <= FULL_DETAIL_SPAN:
            return index, values
        max_points = MEDIUM_BUCKETS if span <= MEDIUM_SPAN else COARSE_BUCKETS
    if n <= max_points:
        return index, values

    n_buckets = max(1, max_points // 2)
    edges = np.linspace(0, n, n_buckets + 1).astype(int)
    idx_out, val_out = [], []
    vals = np.asarray(values, dtype=float)
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        chunk = vals[a:b]
        if np.all(np.isnan(chunk)):
            mid = a + (b - a) // 2
            idx_out.append(index[mid]); val_out.append(np.nan)
            continue
        i_min = a + int(np.nanargmin(chunk))
        i_max = a + int(np.nanargmax(chunk))
        for i in sorted((i_min, i_max)):
            idx_out.append(index[i]); val_out.append(vals[i])
    return pd.DatetimeIndex(idx_out), np.array(val_out)


def point_budget(span):
    if span is None or span <= FULL_DETAIL_SPAN:
        return "every point (full detail)"
    if span <= MEDIUM_SPAN:
        return f"min/max envelope, ~{MEDIUM_BUCKETS:,} pts per trace"
    return f"min/max envelope, ~{COARSE_BUCKETS:,} pts per trace"


def qc_figure(df, keys, removed_intervals, x_range=None,
              show_station_h2o=True, show_solar=False, height=760):
    """Stacked CH4 / CO2 / H2O with a shared, zoomable x-axis.

    Kept data is drawn in colour; points switched off by the measure key
    are drawn faintly in red underneath, so you can always see what you
    removed and put it back.
    """
    span = None
    view = df
    if x_range is not None:
        lo, hi = x_range
        view = df.loc[lo:hi]
        span = hi - lo
    elif len(df):
        span = df.index[-1] - df.index[0]

    rows = 3
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.045,
                        subplot_titles=[LABELS[c] for c in ("CH4_dry", "CO2_dry", "H2O")])

    for r, col in enumerate(("CH4_dry", "CO2_dry", "H2O"), start=1):
        if col not in view.columns:
            continue
        original = view[col]
        key = keys[col].reindex(view.index).fillna(1)
        kept = original.where(key == 1)
        cut = original.where(key == 0)

        xi, yi = decimate(view.index, cut.to_numpy(), span)
        if np.any(~np.isnan(yi)):
            fig.add_trace(go.Scattergl(
                x=xi, y=yi, mode="markers", name=f"{col} removed",
                marker=dict(size=3, color=COLORS["removed"]),
                opacity=0.5, showlegend=(r == 1),
                hovertemplate="REMOVED<br>%{x|%Y-%m-%d %H:%M:%S}<br>%{y:.4f}<extra></extra>",
            ), row=r, col=1)

        xk, yk = decimate(view.index, kept.to_numpy(), span)
        fig.add_trace(go.Scattergl(
            x=xk, y=yk, mode="lines", name=col,
            line=dict(color=COLORS[col], width=1), showlegend=(r == 1),
            hovertemplate="%{x|%Y-%m-%d %H:%M:%S}<br>%{y:.4f}<extra></extra>",
        ), row=r, col=1)

        if col == "H2O" and show_station_h2o and "wstation_H2O" in view.columns:
            xs, ys = decimate(view.index, view["wstation_H2O"].to_numpy(), span)
            fig.add_trace(go.Scattergl(
                x=xs, y=ys, mode="lines", name="Station H2O (Clausius-Clapeyron)",
                line=dict(color=COLORS["wstation_H2O"], width=1, dash="dot"),
                hovertemplate="station %{y:.4f} %<extra></extra>",
            ), row=r, col=1)

        if col == "CH4_dry" and show_solar and "solar_radiation" in view.columns:
            s = view["solar_radiation"]
            rng = np.nanmax(s) - np.nanmin(s)
            if rng and np.isfinite(rng):
                lo_y, hi_y = np.nanmin(kept), np.nanmax(kept)
                if np.isfinite(lo_y) and np.isfinite(hi_y):
                    scaled = lo_y + (s - np.nanmin(s)) / rng * (hi_y - lo_y)
                    xs, ys = decimate(view.index, scaled.to_numpy(), span)
                    fig.add_trace(go.Scattergl(
                        x=xs, y=ys, mode="lines", name="Solar radiation (scaled)",
                        line=dict(color=COLORS["solar_radiation"], width=1),
                        opacity=0.65, hoverinfo="skip",
                    ), row=r, col=1)

    for (t0, t1, _sp) in removed_intervals:
        for r in range(1, rows + 1):
            fig.add_vrect(x0=t0, x1=t1, fillcolor=COLORS["removed"],
                          opacity=0.13, line_width=0, row=r, col=1)

    fig.update_layout(
        height=height, margin=dict(l=60, r=20, t=50, b=40),
        dragmode="select", selectdirection="h", hovermode="x unified",
        legend=dict(orientation="h", y=1.06, x=0),
        uirevision="keep-zoom",     # zoom survives reruns
    )
    fig.update_xaxes(title_text="Date and time (local, after Picarro clock correction)",
                     row=rows, col=1)
    if x_range is not None:
        fig.update_xaxes(range=[x_range[0], x_range[1]])
    return fig


def diurnal_figure(scatter_x, scatter_y, centres, means, stds, label, color):
    """Igor MakeCH4DiurnalProfile / MakeCO2DiurnalProfile."""
    fig = go.Figure()
    if len(scatter_x):
        xs, ys = (scatter_x, scatter_y)
        if len(xs) > 40000:
            step = len(xs) // 40000 + 1
            xs, ys = xs[::step], ys[::step]
        fig.add_trace(go.Scattergl(x=xs, y=ys, mode="markers", name="all points",
                                   marker=dict(size=2, color=color, opacity=0.25)))
    fig.add_trace(go.Scatter(
        x=centres, y=means, mode="markers+lines", name="hourly mean",
        marker=dict(size=7, color="black", symbol="circle-open"),
        line=dict(color="black", width=1),
        error_y=dict(type="data", array=stds, visible=True, color="black", width=3),
    ))
    fig.update_layout(height=380, margin=dict(l=60, r=20, t=40, b=40),
                      xaxis=dict(title="Time of day (Eastern)", range=[0, 24],
                                 tickmode="array", tickvals=list(range(0, 25, 3))),
                      yaxis_title=label, showlegend=False)
    return fig


def h2o_comparison_figure(df, keys):
    """Igor H2OComparison(): Picarro H2O vs weather-station H2O."""
    fig = go.Figure()
    span = df.index[-1] - df.index[0] if len(df) else None
    kept = df["H2O"].where(keys["H2O"].reindex(df.index).fillna(1) == 1)
    x, y = decimate(df.index, kept.to_numpy(), span)
    fig.add_trace(go.Scattergl(x=x, y=y, mode="lines", name="Picarro H2O",
                               line=dict(color=COLORS["H2O"], width=1)))
    if "wstation_H2O" in df.columns:
        xs, ys = decimate(df.index, df["wstation_H2O"].to_numpy(), span)
        fig.add_trace(go.Scattergl(x=xs, y=ys, mode="lines",
                                   name="Weather Station H2O",
                                   line=dict(color=COLORS["wstation_H2O"], width=1)))
    fig.update_layout(height=420, margin=dict(l=60, r=20, t=40, b=40),
                      yaxis_title="H2O (%)", xaxis_title="Date and time (local)",
                      legend=dict(orientation="h", y=1.1, x=0), uirevision="h2o")
    return fig

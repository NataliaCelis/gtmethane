# Picarro QC

A browser-based replacement for `ProcessPicarro.ipf` (Igor Pro) — load a month
of Picarro G2301 data, look at it, remove the bad stretches by dragging on the
plot, and export the 10-minute averaged final table.

Every formula from the Igor procedure is reproduced exactly. See
[Fidelity to the Igor procedure](#fidelity-to-the-igor-procedure) for the
line-by-line mapping, and [What is *not* a copy](#what-is-not-a-copy) for the
honest list of differences.

---

## Install and run

```bash
./run.sh
```

That creates a virtual environment, installs dependencies, and opens the app in
your browser. Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

---

## Workflow — one month at a time

### 1. Load (sidebar)

| Source | What it expects |
|---|---|
| **Monthly .zip** | `yyyy-mm.zip` containing day folders of `.dat` files |
| **Concatenated .csv** | `EPOCH_TIME,CO2_dry,CH4_dry,H2O` (or the `DATE_TIME_UTC`/`EPOCH_MIN` export) |
| **Folder path** | any folder tree containing `.dat` files, searched recursively |

Then load a **weather CSV** with `Timestamp` (unix) and `Dewpoint`.
`Barometer` and `Solar Radiation Sensor` are used if present.
Files with CR-only line endings are handled.

### 2. Timezone

The Picarro clock runs 7 hours off. Pick the timezone in the sidebar:

- **EST** → −8 h (Igor `ESTProcessPicarroTime`)
- **EDT** → −7 h (Igor `EDTProcessPicarroTime`)

EDT runs from the second Sunday in March to the first Sunday in November, 2:00 AM.
As the Igor procedure advises, **process March and November in two parts.**

### 3. Edit

Drag left-to-right across any panel to select a time range, then **Remove** or
**Restore**. Nothing is ever deleted from the file: removal sets that range's
*measure key* to 0, exactly as in Igor, and removed points stay visible in faint
red so you can always put them back.

- Apply to **all** species at once, or to CH₄ / CO₂ / H₂O individually
  (Igor lets you edit each graph separately, then Sync Edits merges them).
- **Undo last edit** steps back through your history.
- **Sync edits** applies `SyncMeasureKeys`: a point removed in any one species
  is removed in all three.
- The full edit history is shown as a table and exported with the workbook.

### 4. Final table

10-minute averages (`XSec = 600`), empty bins filled with the missing-data code
**−888**, downloadable as `.xlsx` (with a second sheet holding your edit log and
a third holding run settings) or `.csv`.

---

## Handling a month of 1 Hz data in a browser

A month at 1 Hz is ~2.6 million points per trace — no browser will draw that.
The number of points sent to the browser is capped based on how far you are
zoomed in:

| Visible window | What you get |
|---|---|
| ≤ 1 day | **every single point**, nothing hidden |
| ≤ 7 days | min/max envelope, ~8,000 points per trace |
| > 7 days | min/max envelope, ~4,000 points per trace |

The envelope matters. Plain stride-sampling (every *N*th point) can step
straight over a one-minute dropout and make it invisible; taking the **min and
max** of each bucket guarantees any spike or dropout still appears as a vertical
excursion no matter how far out you are. Verified: a 60-second dropout inside
600,000 points survives compression to 8,000 points.

The current window and point budget are always printed above the plot.

---

## Fidelity to the Igor procedure

| Igor | Here | Status |
|---|---|---|
| `ProcessPicarroTime` / `ESTProcessPicarroTime` / `EDTProcessPicarroTime` | `processing.apply_picarro_offset` | −8 h / −7 h, identical |
| `ConvertToPercentH2O` | `processing.convert_to_percent_h2o` | identical constants and formula |
| `utc_to_est` / `utc_to_edt` | `processing.utc_to_est` / `utc_to_edt` | −5 h / −4 h, identical |
| `CO2wet_to_dry` | `processing.co2_wet_to_dry` | identical (Instrument 2 only) |
| `EditCH4` / `EditCO2` / `EditH2O` measure keys | `new_measure_key`, `apply_key` | same 1/0 key and NaN dependency |
| `Make_zeros_between_cursors` / `Make_ones_between_cursors` | `zeros_between` / `ones_between` | same, driven by drag-select |
| `SyncMeasureKeys` | `processing.sync_measure_keys` | identical logic |
| `AveragCH4` / `AveragCO2` / `AveragH2O` (`XSec=600`, `minNumNonNans=1`) | `processing.averag_seconds` | identical bin edges and rule |
| `ReplaceNaNs` | `processing.replace_nans` | −888 |
| `FinalTable` | `processing.final_table` | same columns |
| `MakeCH4DiurnalProfile` / `MakeCO2DiurnalProfile` | `processing.diurnal` | `mod(t, 86400)`, hourly mean ± std |
| `H2OComparison` | `plotting.h2o_comparison_figure` | same two traces |
| `SolarRadiation` | solar overlay checkbox on the Edit tab | same time-alignment check |

### Clausius-Clapeyron

Copied verbatim from `ConvertToPercentH2O()`:

```
dewpoint_K   = (dewpoint_F − 32) × (5/9) + 273.15
pressure_hPa = barometer_inHg × 33.8639
e_actual     = e0 × exp((Lv/Rv) × (1/T0 − 1/dewpoint_K))
wstation_H2O = (e_actual / pressure_hPa) × 100
```

with `e0 = 6.1078 hPa`, `T0 = 273.16 K`, `Lv = 2.501×10⁶ J/kg`, `Rv = 461.5 J/(kg·K)`.

Verified against a hand-computed value to 12 decimal places.
If the weather file has no `Barometer` column, a fixed `975.0 hPa` is used and
the app says so — Igor always expects a barometer, so this is a fallback, not
part of the port.

---

## What is *not* a copy

- **No automatic bad-data detection.** Like Igor, you decide what to remove.
  There are no thresholds proposing edits.
- **Instrument 2 (G2210-i) is not ported.** The `C2H6`, `δ¹³C-CH₄`,
  `SyncMeasureKeys2`, `ExpandCoarseKey`, and `FinalTable2` paths are absent.
  `CO2wet_to_dry` is included for completeness.
- **Diurnal profiles** group by hour rather than calling Igor's `AveragSecs`;
  the result is equivalent, the code is not.
- **Selection** is drag-on-plot rather than Igor's A/B cursors.
- The app adds file loading (zip / folder / CSV) that Igor does not do — Igor
  assumes waves are already in the experiment.

---

## Layout

```
picarro_qc/
├── app.py                 Streamlit app
├── concat_month.py        CLI concatenation
├── picarro/
│   ├── processing.py      Igor formula ports (C-C, keys, averaging)
│   ├── concat.py          zip / folder / CSV / weather loading
│   └── plotting.py        Plotly figures + envelope decimation
├── sample_data/           synthetic month for testing the app
├── requirements.txt
└── run.sh
```

## Command-line concatenation

```bash
python concat_month.py --zip "/path/to/2025-06.zip" --out 2025-06_concat.csv
python concat_month.py --folder "2025/06/Raw Data" --out 2025-06_concat.csv
```

## Sample data

`sample_data/` holds a synthetic June 2025 month (3 days at 1 Hz, with a
deliberate 35-minute power dropout at 03:00 on the 2nd, and a weather file whose
dewpoint puts station H₂O about 0.6 % below the Picarro). Load
`2025-06.zip` + `weather_2025-06.csv` with timezone **EDT** to try the workflow.

## Notes

- Removed points are never written to the final table — they become −888.
- Zoom level survives edits, so removing a stretch does not throw you back out
  to the whole month.
- The exported workbook records your edit log and run settings, so a month can
  be reproduced later.

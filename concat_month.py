#!/usr/bin/env python3
"""
Command-line monthly concatenation -- the original zip script, using the
shared loader so the CLI and the app behave identically.

    python concat_month.py --zip "/path/2025-06.zip" --out ./2025-06_concat.csv
    python concat_month.py --folder "2025/06/Raw Data" --out ./2025-06_concat.csv
"""
import argparse
from pathlib import Path
from picarro import concat


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--zip", dest="zip_path")
    g.add_argument("--folder")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    def prog(f, m):
        print(f"\r  {f*100:5.1f}%  {m}", end="", flush=True)

    if a.zip_path:
        print(f"Reading archive {a.zip_path} …")
        df, n, warns = concat.concat_zip(Path(a.zip_path), progress=prog)
    else:
        print(f"Reading folder {a.folder} …")
        df, n, warns = concat.concat_folder(a.folder, progress=prog)

    print(f"\nRead {n} .dat files.")
    for w in warns[:20]:
        print("  WARNING:", w)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Writing {len(df):,} rows -> {out}")
    print("Done ✓")


if __name__ == "__main__":
    main()

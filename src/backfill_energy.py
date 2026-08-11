"""Reconcile every EIA fuel CSV onto one identical hourly index.

Three passes:

  1. Dedupe. The old fetch asked for [day T00, day+1 T00] and EIA treats both
     bounds as inclusive, so every day re-fetched the next day's hour 00.
  2. Re-query. For each contiguous run of missing hours, ask the API again for
     exactly that window. Some gaps are transient on EIA's side and fill in
     later; this is the only way to tell those from permanent ones.
  3. Reindex. Anything the API still will not serve becomes an explicit NaN row
     so all twelve files share one index, same length, same timestamps.

Values are never invented. A NaN here means EIA has no number for that hour.
"""

import os
import glob

import dotenv
import pandas as pd
import requests

dotenv.load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "data", "energy")

API_KEY = os.getenv("EIA_API_KEY")
URL = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
RESPONDENT = "CAL"


def load_clean(path):
    """One fuel file, deduped, indexed by tz-aware UTC hour."""
    df = pd.read_csv(path)
    period = pd.to_datetime(df["period"])
    if period.dt.tz is None:
        period = period.dt.tz_localize("UTC")
    df["period"] = period
    return (df.drop_duplicates(subset="period")
              .set_index("period")
              .sort_index())


def contiguous_blocks(stamps):
    """Group a sorted DatetimeIndex into runs of consecutive hours."""
    blocks = []
    for t in stamps:
        if blocks and t - blocks[-1][1] == pd.Timedelta("1h"):
            blocks[-1][1] = t
        else:
            blocks.append([t, t])
    return [(a, b) for a, b in blocks]


def query(fuel, start, end):
    """Ask EIA for one window. Returns {timestamp: value}."""
    resp = requests.get(URL, params={
        "api_key": API_KEY,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": RESPONDENT,
        "facets[fueltype][]": fuel,
        "start": start.strftime("%Y-%m-%dT%H"),
        "end": end.strftime("%Y-%m-%dT%H"),
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": 5000,
    }, timeout=60)
    resp.raise_for_status()
    body = resp.json()["response"]

    out = {}
    for row in body["data"]:
        if row["value"] is None:
            continue
        out[pd.Timestamp(row["period"]).tz_localize("UTC")] = float(row["value"])
    return out, int(body.get("total", 0))


def main():
    paths = sorted(glob.glob(os.path.join(RAW_DIR, "eia_data_*.csv")))
    frames = {os.path.basename(p).replace("eia_data_", "").replace(".csv", ""):
              load_clean(p) for p in paths}

    # The canonical index spans the widest range any fuel reports, at 1h steps.
    lo = min(f.index.min() for f in frames.values())
    hi = max(f.index.max() for f in frames.values())
    full = pd.date_range(lo, hi, freq="1h", tz="UTC")
    print(f"canonical index: {lo} -> {hi}  ({len(full)} hours)\n")

    recovered_total = 0
    report = {}

    for fuel, df in frames.items():
        missing = full.difference(df.index)
        if len(missing) == 0:
            report[fuel] = (0, 0, 0)
            print(f"{fuel:5s} complete, nothing to do")
            continue

        blocks = contiguous_blocks(missing)
        print(f"{fuel:5s} {len(missing):4d} missing hours in {len(blocks)} block(s)")

        recovered = {}
        for start, end in blocks:
            values, total = query(fuel, start, end)
            hours = int((end - start).total_seconds() // 3600) + 1
            got = {t: v for t, v in values.items() if t in set(missing)}
            recovered.update(got)
            print(f"        {start:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M}  "
                  f"({hours:4d}h)  EIA total={total:4d}  recovered={len(got):4d}")

        if recovered:
            add = pd.DataFrame(index=pd.DatetimeIndex(sorted(recovered)))
            add["value"] = [recovered[t] for t in add.index]
            df = pd.concat([df, add]).sort_index()
            frames[fuel] = df

        recovered_total += len(recovered)
        report[fuel] = (len(missing), len(recovered), len(missing) - len(recovered))

    print(f"\nrecovered {recovered_total} hours from the API\n")

    # Reindex everything onto the canonical index and restore the constant
    # metadata columns, so only "value" is ever blank.
    for fuel, df in frames.items():
        out = df.reindex(full)
        out.index.name = "period"

        for col, val in [("respondent", RESPONDENT),
                         ("respondent-name", "California"),
                         ("fueltype", fuel),
                         ("value-units", "megawatthours")]:
            if col in out.columns:
                out[col] = val
        if "type-name" in out.columns:
            known = df["type-name"].dropna()
            if len(known):
                out["type-name"] = known.iloc[0]

        path = os.path.join(RAW_DIR, f"eia_data_{fuel}.csv")
        out.to_csv(path)
        frames[fuel] = out

    print(f"{'fuel':5s} {'rows':>6s} {'missing':>8s} {'recovered':>10s} "
          f"{'still NaN':>10s}")
    for fuel in sorted(frames):
        miss, rec, left = report[fuel]
        print(f"{fuel:5s} {len(frames[fuel]):6d} {miss:8d} {rec:10d} {left:10d}")

    lengths = {len(f) for f in frames.values()}
    indexes_match = all(f.index.equals(full) for f in frames.values())
    print(f"\nall files same length : {len(lengths) == 1} ({lengths})")
    print(f"all indexes identical : {indexes_match}")


if __name__ == "__main__":
    main()

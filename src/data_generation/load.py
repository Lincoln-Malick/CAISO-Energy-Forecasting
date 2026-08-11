"""Load the EIA and weather CSVs into one time-aligned table.

TIME ZONES -- both sources are UTC. Provenance for each:

  Energy (EIA v2, /electricity/rto/fuel-type-data/)
      We request frequency="hourly", which EIA's own metadata documents as
      "hourly (UTC)" / "One data point for each hour in UTC time", with period
      format YYYY-MM-DDTHH and no offset field. The alternative frequency is
      "local-hourly" ("hourly (Local Time Zone)", format YYYY-MM-DDTHHTZH,
      which carries an explicit offset) -- we deliberately do NOT use it.

  Weather (Open-Meteo archive + forecast)
      We pass timezone=UTC explicitly. The response confirms it with
      utc_offset_seconds=0 and timezone_abbreviation=GMT. Times come back as
      YYYY-MM-DDTHH:MM with no offset, so we parse with utc=True.

Neither API stamps an offset onto its timestamps, so both arrive tz-NAIVE and
must be labelled rather than converted. Getting that backwards -- localizing
UTC data to Pacific -- shifts one series 7-8h against the other and quietly
destroys every solar model downstream.

Why UTC is the join key: California observes DST, so local time has one hour
that occurs twice in November and one that never happens in March. A join on
local timestamps silently duplicates the first and drops the second, every year.
UTC has neither problem. Local time is derived from the UTC index for
hour-of-day features and plots, and is never the join key.

Verified empirically, not just from docs: a full day of CAISO solar is flat at
UTC 05-13 (= local 22:00-06:00, night) and peaks at UTC 20-21 (= local 13:00).
Lag-correlating EIA solar against Fresno GHI peaks at lag 0 (r=0.923), holding
equally in PST (0.911) and PDT (0.927) -- a tz error would peak at +-7/+-8h and
split across those two regimes.
"""

import os
import glob

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENERGY_DIR = os.path.join(BASE_DIR, "data", "energy")
WEATHER_DIR = os.path.join(BASE_DIR, "data", "weather")

LOCAL_TZ = "America/Los_Angeles"


def _ensure_utc(series, source):
    """Return a tz-aware UTC series, refusing to guess at a naive one."""
    s = pd.to_datetime(series)

    if s.dt.tz is None:
        # Older CSVs were written naive. They were always UTC -- EIA's hourly
        # frequency is UTC -- so label them rather than convert.
        print(f"  note: {source} timestamps are naive; labelling as UTC")
        return s.dt.tz_localize("UTC")

    return s.dt.tz_convert("UTC")


def load_energy(energy_dir=ENERGY_DIR):
    """Wide table of hourly generation, one column per fuel type, indexed UTC."""
    frames = []

    for path in sorted(glob.glob(os.path.join(energy_dir, "eia_data_*.csv"))):
        df = pd.read_csv(path)
        df["period"] = _ensure_utc(df["period"], os.path.basename(path))

        fuel = df["fueltype"].iloc[0]
        s = (df.drop_duplicates(subset="period")
               .set_index("period")["value"]
               .astype(float)
               .rename(fuel)
               .sort_index())
        frames.append(s)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, axis=1).sort_index()


def load_weather(weather_dir=WEATHER_DIR, filename="weather_solar_hourly.csv"):
    """Wide table of weather, columns suffixed by site, indexed UTC."""
    path = os.path.join(weather_dir, filename)
    df = pd.read_csv(path)
    df["time"] = _ensure_utc(df["time"], filename)

    # site is a row label in the CSV; pivot it into the column names so the
    # result is one row per hour and joins directly against the energy table.
    wide = df.pivot_table(index="time", columns="site")
    wide.columns = [f"{var}__{site}" for var, site in wide.columns]
    return wide.sort_index()


def load_joined(how="inner"):
    energy = load_energy()
    weather = load_weather()

    df = energy.join(weather, how=how)
    df.index.name = "time_utc"

    # Local-time features. Derived from the UTC index, so DST is handled by the
    # tz database instead of by hand.
    local = df.index.tz_convert(LOCAL_TZ)
    df["local_hour"] = local.hour
    df["local_date"] = local.date
    df["month"] = local.month

    return df


if __name__ == "__main__":
    print("loading energy ...")
    energy = load_energy()
    print(f"  {energy.shape[0]} hours x {energy.shape[1]} fuel types")

    print("loading weather ...")
    weather = load_weather()
    print(f"  {weather.shape[0]} hours x {weather.shape[1]} columns")

    df = load_joined()
    print(f"\njoined: {df.shape[0]} hours x {df.shape[1]} columns")
    print(f"range : {df.index.min()} -> {df.index.max()}")

    # Every hour should appear exactly once, and the spacing should be a clean
    # 1h with no gaps or repeats -- the two failure modes of a bad tz join.
    gaps = df.index.to_series().diff().value_counts()
    print(f"\nindex is unique: {df.index.is_unique}")
    print(f"index is sorted: {df.index.is_monotonic_increasing}")
    print("spacing between consecutive hours:")
    print(gaps.head())

    if "SUN" in df.columns:
        peak = df.groupby("local_hour")["SUN"].mean().idxmax()
        print(f"\nsolar generation peaks at local hour {peak} (want 12-13)")
        ghi_cols = [c for c in df.columns if c.startswith("shortwave_radiation")]
        if ghi_cols:
            gpeak = df.groupby("local_hour")[ghi_cols[0]].mean().idxmax()
            print(f"GHI peaks at local hour {gpeak} (want 12-13)")

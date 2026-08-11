import os
import time
from datetime import date, timedelta

import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "data", "weather")
os.makedirs(RAW_DIR, exist_ok=True)

# Two endpoints, one variable schema. The archive is ERA5 reanalysis and is the
# better number, but it lags ~5 days behind real time. The forecast endpoint
# backfills that gap (past_days) and is also what we'll call at inference time.
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

START = date(2025, 1, 1)


def utc_yesterday():
    """Yesterday's date in UTC.

    Deliberately not date.today(): that is the machine's LOCAL date, which in
    California is the previous day for the first 7-8 hours of every UTC day.
    Everything in this project is UTC, so the cutoff must be too.

    Ending yesterday rather than today keeps the series to whole 24h days.
    Today is still in progress -- the last hours are forecast rather than
    observed, and re-running mid-afternoon would silently change rows that a
    morning run had already written.
    """
    return pd.Timestamp.now(tz="UTC").date() - timedelta(days=1)

# Same three sites as the meteostat version, minus the station matching --
# Open-Meteo is gridded, so we ask for a lat/lon directly and always get a
# complete series. No nearest-station search, no completeness holes.
SITES = {
    "la_urban": (34.0522, -118.2437),
    "fresno_solar": (36.7378, -119.7871),
    "tehachapi_wind": (35.1322, -118.4489),
}

# The three irradiance components are the reason we're here. They are not
# interchangeable: GHI drives fixed-tilt panels, DNI drives trackers and CSP,
# and DHI is the scattered light that keeps output non-zero under overcast --
# GHI alone overpredicts on cloudy hours. All are W/m², mean over the preceding
# hour. Wind is taken at 100m because that's turbine hub height; the 10m value
# understates it badly in Tehachapi.
HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "wind_speed_100m",
    "wind_direction_100m",
    "shortwave_radiation",       # GHI
    "direct_normal_irradiance",  # DNI
    "diffuse_radiation",         # DHI
]

# Everything is fetched and stored in UTC so it joins cleanly against the EIA
# series (EIA frequency="hourly" is documented as UTC). Convert to
# America/Los_Angeles only for plotting or for building hour-of-day features,
# where local solar time is what actually matters.
TIMEZONE = "UTC"

# Ask for epoch seconds rather than ISO strings. This is the actual guarantee:
# an epoch is an absolute instant with no zone attached, so there is nothing to
# mislabel. ISO strings come back WITHOUT an offset ("2025-06-01T00:00"), and
# parsing those with utc=True merely *stamps* UTC onto whatever local clock the
# API happened to use -- which is silently wrong, and wrong by a whole 7-8h,
# the moment TIMEZONE is changed to anything else.
TIMEFORMAT = "unixtime"


def _get(url, params, retries=3):
    # Open-Meteo is generous on the free tier but will 429 if you hammer it.
    # Back off and retry rather than losing a long backfill to one blip.
    for attempt in range(1, retries + 1):
        resp = requests.get(url, params=params, timeout=60)

        if resp.status_code == 429 and attempt < retries:
            wait = 5 * attempt
            print(f"rate limited, retrying in {wait}s ... ", end="", flush=True)
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp.json()

    raise RuntimeError(f"giving up on {url} after {retries} attempts")


def _to_frame(payload, site):
    # Fail loudly on config drift. The epoch parse below is correct whatever
    # zone was requested, but a non-zero offset means the start_date/end_date
    # window no longer delimits UTC days, so the series would silently start
    # and end at the wrong instants.
    offset = payload.get("utc_offset_seconds")
    if offset != 0:
        raise ValueError(
            f"expected a UTC response, but Open-Meteo reported "
            f"utc_offset_seconds={offset} (timezone={payload.get('timezone')!r}). "
            f"TIMEZONE must stay 'UTC' -- see the module docstring."
        )

    # The API returns columns, not records: {"time": [...], "temperature_2m": [...]}
    # which is already the shape DataFrame wants.
    df = pd.DataFrame(payload["hourly"])

    # unit="s" because of TIMEFORMAT above: absolute instants, not wall clocks.
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["site"] = site
    return df.set_index(["site", "time"]).sort_index()


def check_utc_hourly(df):
    """Post-condition: a tz-aware UTC index with exactly one row per hour.

    Raises rather than warns. A timezone fault does not corrupt the numbers,
    only their labels, so nothing downstream will ever crash on it -- it just
    quietly trains the model against the wrong hour's sunlight.
    """
    times = df.index.get_level_values("time")

    if times.tz is None or str(times.tz) != "UTC":
        raise ValueError(f"time index is not tz-aware UTC (got tz={times.tz})")

    for site, group in df.groupby(level="site"):
        t = group.index.get_level_values("time")

        if not t.is_monotonic_increasing:
            raise ValueError(f"{site}: time index is not sorted")
        if not t.is_unique:
            raise ValueError(f"{site}: duplicate timestamps")

        gaps = t.to_series().diff().dropna().unique()
        if len(gaps) > 1 or (len(gaps) == 1 and gaps[0] != pd.Timedelta("1h")):
            raise ValueError(f"{site}: expected uniform 1h spacing, saw {gaps}")

    return True


def fetch_archive(site, lat, lon, start=START, end=None):
    end = end or utc_yesterday()
    payload = _get(
        ARCHIVE_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": ",".join(HOURLY_VARS),
            "timezone": TIMEZONE,
            "timeformat": TIMEFORMAT,
        },
    )
    return _to_frame(payload, site)


def fetch_recent(site, lat, lon, past_days=92, forecast_days=0):
    # past_days maxes out at 92. forecast_days > 0 gives you the forward-looking
    # window -- that's the call you make in production to actually predict.
    payload = _get(
        FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "past_days": past_days,
            "forecast_days": forecast_days,
            "hourly": ",".join(HOURLY_VARS),
            "timezone": TIMEZONE,
            "timeformat": TIMEFORMAT,
        },
    )
    return _to_frame(payload, site)


def fetch_all(sites=SITES, start=START, end=None, past_days=92, forecast_days=0):
    end = end or utc_yesterday()

    # The forecast endpoint always returns today (and beyond, if forecast_days
    # is set), so trimming has to happen after the merge rather than by asking
    # for less. Inclusive of end's final hour, so the series ends on a whole day.
    cutoff = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23)
    frames = []

    for name, (lat, lon) in sites.items():
        print(f"{name:16s} archive ... ", end="", flush=True)
        archive = fetch_archive(name, lat, lon, start=start, end=end)
        print(f"{len(archive)} rows | recent ... ", end="", flush=True)

        recent = fetch_recent(name, lat, lon, past_days, forecast_days)
        print(f"{len(recent)} rows")

        # Archive wins on overlap -- ERA5 reanalysis beats the forecast model for
        # hours that already happened -- but only where it actually has a value.
        # combine_first falls back to `recent` cell by cell, so if the archive
        # ever returns a NaN tail it can't silently outrank a real forecast.
        merged = archive.combine_first(recent)
        merged = merged[merged.index.get_level_values("time") <= cutoff]
        frames.append(merged)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames).sort_index()
    check_utc_hourly(out)
    return out


def daylight_sanity_check(df):
    # Cheap correctness check: GHI must be ~0 at local midnight and clearly
    # positive at local noon. If this fails, the timezone handling is wrong --
    # a silent UTC/local mixup here would quietly wreck every solar model
    # downstream, and it is very hard to spot once it's a feature column.
    local = df.reset_index()
    local["hour"] = local["time"].dt.tz_convert("America/Los_Angeles").dt.hour

    night = local.loc[local["hour"] == 0, "shortwave_radiation"].mean()
    noon = local.loc[local["hour"] == 12, "shortwave_radiation"].mean()
    return night, noon


if __name__ == "__main__":
    weather = fetch_all()

    out_path = os.path.join(RAW_DIR, "weather_solar_hourly.csv")
    weather.to_csv(out_path)

    print(f"\n{len(weather)} rows -> {out_path}")
    print(f"range: {weather.index.get_level_values('time').min()} "
          f"-> {weather.index.get_level_values('time').max()}")

    missing = weather.isna().sum()
    if missing.any():
        print("\nmissing values:")
        print(missing[missing > 0])
    else:
        print("no missing values")

    night, noon = daylight_sanity_check(weather)
    print(f"\nmean GHI at local midnight: {night:.1f} W/m² (want ~0)")
    print(f"mean GHI at local noon:     {noon:.1f} W/m² (want a few hundred)")

import os
import meteostat as ms
from datetime import date
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "data", "weather")
os.makedirs(RAW_DIR, exist_ok=True)

START = date(2025, 1, 1)
END = pd.Timestamp.today().normalize()

# One site per thing we're trying to predict: urban load, Central Valley solar,
# Tehachapi wind. A single downtown-LA station can't explain generation 200 miles away.
SITES = {
    "la_urban": ms.Point(34.0522, -118.2437, 100),
    "fresno_solar": ms.Point(36.7378, -119.7871, 94),
    "tehachapi_wind": ms.Point(35.1322, -118.4489, 1200),
}


def find_stations(sites=SITES, per_site=3, radius=100000):
    frames = []
    for name, point in sites.items():
        near = ms.stations.nearby(point, radius=radius, limit=per_site)
        frames.append(near.assign(site=name))

    df = pd.concat(frames)
    # A station can be nearest to two sites; keep the first claim on it
    df = df[~df.index.duplicated(keep="first")]
    return df


def fetch_weather(station_ids, start=START, end=END):
    # One station at a time so there's progress output. Meteostat downloads a
    # gzipped CSV per station per year and caches it in ~/.meteostat/cache, so
    # a re-run only pays for what it hasn't already fetched.
    ids = list(station_ids)
    frames = []

    for i, sid in enumerate(ids, 1):
        print(f"[{i}/{len(ids)}] {sid} ... ", end="", flush=True)
        ts = ms.hourly([sid], start, end)
        df = ts.fetch(fill=True, location=True)

        if df is None or df.empty:
            print("no data")
            continue

        if "station" not in df.index.names:
            df["station"] = sid

        print(f"{len(df)} rows, completeness {ts.completeness()}")
        frames.append(df)

    return pd.concat(frames) if frames else pd.DataFrame()


if __name__ == "__main__":
    stations_df = find_stations()
    print(f"{len(stations_df)} stations across {len(SITES)} sites:")
    print(stations_df[["name", "site", "latitude", "longitude", "distance"]])

    weather_data = fetch_weather(stations_df.index)

    stations_path = os.path.join(RAW_DIR, "weather_stations.csv")
    hourly_path = os.path.join(RAW_DIR, "weather_hourly.csv")
    stations_df.to_csv(stations_path)
    weather_data.to_csv(hourly_path)

    print(f"\n{len(stations_df)} stations -> {stations_path}")
    print(f"{len(weather_data)} rows -> {hourly_path}")

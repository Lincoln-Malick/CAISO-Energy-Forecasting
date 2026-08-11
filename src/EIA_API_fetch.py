import os
import dotenv
import requests as re
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import asyncio
dotenv.load_dotenv()


API_key = os.getenv("EIA_API_KEY")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "data", "energy")
os.makedirs(RAW_DIR, exist_ok=True)



def fetch_one_day(day, fueltype, respondent="CAL", frequency="hourly"):
    if day.day%5==0:
        print(f"Fetching data for {fueltype}:", day.strftime("%Y-%m-%d"))
    url = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
    params = {
    "api_key": API_key,
    "frequency": frequency,
    "data[0]": "value",
    "facets[respondent][]": respondent,
    "facets[fueltype][]": fueltype,
    # EIA treats BOTH start and end as inclusive, so asking for day+1T00 also
    # returns the next day's first hour -- which the next request fetches again.
    # Ending at T23 gives exactly 24 hours and no overlap at the day boundary.
    "start": day.strftime("%Y-%m-%dT%H"),
    "end": (day + timedelta(hours=23)).strftime("%Y-%m-%dT%H"),
    "sort[0][column]": "period",
    "sort[0][direction]": "desc",
    "offset": 0,
    "length": 5000,
    }
    response = re.get(url, params = params)

    if response.status_code == 200:
        data = response.json()
        return data["response"]['data']
    else:

        print(f"Error: {response.status_code} - {response.text}")
        raise Exception(f"Error: {response.status_code} - {response.text}")

async def fetch_eia_data(start_date, end_date, fueltype, respondent="CAL", frequency="hourly", max_concurrent=8):
    days = []
    while start_date < end_date:
        days.append(start_date)
        start_date += relativedelta(days=1)

    sem = asyncio.Semaphore(max_concurrent)

    async def one(day):
        async with sem:
            return await asyncio.to_thread(fetch_one_day, day, fueltype, respondent, frequency)

    chunks = await asyncio.gather(*(one(d) for d in days))

    df_e = []
    for c in chunks:
        df_e += c

    df = pd.DataFrame(df_e)
    # frequency="hourly" is UTC (EIA's "local-hourly" is the local-time variant).
    # utc=True keeps it tz-AWARE so the CSV records "+00:00" on every row -- a
    # naive timestamp is an invitation for someone downstream to localize it to
    # Pacific and shift the whole series 8 hours against the weather data.
    df["period"] = pd.to_datetime(df["period"], format="%Y-%m-%dT%H", utc=True)
    # Belt-and-braces against the inclusive-end overlap fixed above, and against
    # any retry that double-appends a day.
    df = df.drop_duplicates(subset="period")
    df = df.sort_values("period", ascending=True).reset_index(drop=True)
    out_path = os.path.join(RAW_DIR, f"eia_data_{fueltype}.csv")
    df.to_csv(out_path, index=False)
    return df

today = pd.Timestamp.now().normalize()

for f in [ 'SNB', 'SUN', 'WAT', 'WND']:
    print(f"Fetching data for fuel type: {f}")
    df = asyncio.run(fetch_eia_data(datetime(2025, 1, 1, 0), today, f))
    print(f"  {len(df)} rows -> eia_data_{f}.csv")

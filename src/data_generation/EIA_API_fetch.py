import os
import dotenv
import requests as re
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import asyncio
dotenv.load_dotenv()


API_key = os.getenv("EIA_API_KEY")

# __file__ is src/data_generation/EIA_API_fetch.py -> three dirname calls climb
# data_generation -> src -> project root, so data/ resolves at the repo root.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DIR = os.path.join(BASE_DIR, "data", "energy")
os.makedirs(RAW_DIR, exist_ok=True)



def fetch_one_day_load(day, dtype="D", respondent="CAL", frequency="hourly"):
    # Load/demand is NOT on the fuel-type endpoint -- it lives on region-data,
    # keyed by `type` instead of `fueltype`. type="D" is actual demand (load);
    # "DF" is the day-ahead demand forecast, "NG" net generation, "TI" interchange.
    # The `dtype` arg lines up with fetch_eia_data's positional `fueltype`, so this
    # drops straight into that orchestrator as fetch_func.
    if day.day % 5 == 0:
        print(f"Fetching load ({dtype}):", day.strftime("%Y-%m-%d"))
    url = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
    params = {
    "api_key": API_key,
    "frequency": frequency,
    "data[0]": "value",
    "facets[respondent][]": respondent,
    "facets[type][]": dtype,
    # Same inclusive-end handling as fetch_one_day_gen: T23 gives exactly 24 hours
    # with no overlap into the next day's first hour.
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

def fetch_one_day_gen(day, fueltype, respondent="CAL", frequency="hourly"):
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

async def fetch_eia_data(start_date, end_date, fueltype, fetch_func = fetch_one_day_gen,respondent="CAL", frequency="hourly", max_concurrent=8):
    days = []
    while start_date < end_date:
        days.append(start_date)
        start_date += relativedelta(days=1)

    sem = asyncio.Semaphore(max_concurrent)

    async def one(day):
        async with sem:
            return await asyncio.to_thread(fetch_func, day, fueltype, respondent, frequency)

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


if __name__ == "__main__":
    today = datetime(2026,8,11)

    # for f in [ 'SNB', 'SUN', 'WAT', 'WND']:
    #     print(f"Fetching data for fuel type: {f}")
    #     df = asyncio.run(fetch_eia_data(datetime(2025, 1, 1, 0), today, f))
    #     print(f"  {len(df)} rows -> eia_data_{f}.csv")

    print("Fetching load (demand)")
    df = asyncio.run(fetch_eia_data(datetime(2025, 1, 1, 0), today, "D", fetch_func=fetch_one_day_load))
    print(f"  {len(df)} rows -> eia_data_D.csv")

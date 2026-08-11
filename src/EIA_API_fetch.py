import os
import dotenv
import requests as re
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
dotenv.load_dotenv()


API_key = os.getenv("EIA_API_KEY")
print("API Key:", API_key)

url = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
params = {
    "api_key": API_key,
    "frequency": "hourly",
    "data[0]": "value",
    "facets[respondent][]": "CAL",
    "start": "2026-08-07T00",
    "end": "2026-08-09T00",
    "sort[0][column]": "period",
    "sort[0][direction]": "desc",
    "offset": 0,
    "length": 5000,
}


def fetch_eia_data(start_date, end_date, fueltype, respondent="CAL", frequency="hourly"):
    df_e = []
    while start_date < end_date:
        if start_date.day%5==0:
            print("Fetching data for:", start_date.strftime("%Y-%m-%d"))
        url = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
        params = {
        "api_key": API_key,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": respondent,
        #"facets[fueltype][]": fueltype,  
        "start": start_date.strftime("%Y-%m-%dT%H"),
        "end": (start_date + relativedelta(days=1)).strftime("%Y-%m-%dT%H"),
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "offset": 0,
        "length": 5000,
        }
        response = re.get(url, params = params)

        if response.status_code == 200:
            data = response.json()
            df_e += data["response"]['data']
        else:

            print(f"Error: {response.status_code} - {response.text}")

        start_date += relativedelta(days=1)
    df = pd.DataFrame(df_e)
    df.to_csv("eia_data.csv", index=False)
    return df

today = pd.Timestamp.now().normalize()

# print(fetch_eia_data(datetime(2025, 1, 1, 0), today, "SUN"))
da = fetch_eia_data(datetime(2026, 8, 8, 0), today, "SUN")
print(da['fueltype'].unique())

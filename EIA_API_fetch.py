import os
import dotenv
import requests as re
import pandas as pd
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
    url = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
    params = {
    "api_key": API_key,
    "frequency": "hourly",
    "data[0]": "value",
    "facets[respondent][]": respondent,
    "facets[fueltype][]": fueltype,  
    "start": start_date,
    "end": end_date,
    "sort[0][column]": "period",
    "sort[0][direction]": "desc",
    "offset": 0,
    "length": 5000,
    }
    response = re.get(url, params = params)

    if response.status_code == 200:
        data = response.json()
        print(data["response"]['data'][0])
        columns = data["response"]['data'][0].keys()
        df = pd.DataFrame(data["response"]['data'], columns=columns)
        
        return df
    else:

        print(f"Error: {response.status_code} - {response.text}")


print(fetch_eia_data("2026-07-07T00", "2026-08-09T00", "SUN"))

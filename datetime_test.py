
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import pandas as pd
start_date = datetime(2025, 1, 1)

today = pd.Timestamp.now().normalize()

while start_date < today:
    start_date += relativedelta(days=1)
    print("Start Date:", start_date.strftime("%Y-%m-%d"))

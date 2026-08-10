import matplotlib.pyplot as plt
import meteostat as ms
from datetime import date
import pandas as pd

POINT = ms.Point(34.0522, -118.2437, 100)  # Los Angeles, CA   
START = date(2026, 7, 1)
END = pd.Timestamp.today().normalize()
stations = ms.stations.nearby(POINT, limit=4)

ts = ms.hourly(ms.Station(id='KCQT0'), START, END)
df = ts.fetch()


print(df.head)
# df.plot(y=[ms.Parameter.TEMP, ms.Parameter.TMIN, ms.Parameter.TMAX])
# plt.show()
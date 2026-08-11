import pandas as pd
weather_comp_df = pd.read_csv("data/weather/weather_solar_hourly.csv")

Fresno_solar_df = weather_comp_df[weather_comp_df["site"] == "fresno_solar"]
print(Fresno_solar_df.head())
LA_urban_df = weather_comp_df[weather_comp_df["site"] == "la_urban"]
print(LA_urban_df.head())
Tehachapi_wind_df = weather_comp_df[weather_comp_df["site"] == "tehachapi_wind"]
print(Tehachapi_wind_df.head())


# Step 0 -- why the next few steps are needed at all.
# pd.concat(axis=1) glues frames together by matching their INDEX, not by
# stacking them in the order you list them. Right now each slice above kept the
# row numbers it had inside weather_comp_df:
#     Fresno    rows     0 .. 14111
#     LA        rows 14112 .. 28223
#     Tehachapi rows 28224 .. 42335
# Those ranges never overlap, so pandas would put each site on its own set of
# rows -- a 42336-row block that is 2/3 empty -- instead of side by side.
print("\nindex ranges before fixing:")
print("  Fresno   ", Fresno_solar_df.index.min(), "..", Fresno_solar_df.index.max())
print("  LA       ", LA_urban_df.index.min(), "..", LA_urban_df.index.max())
print("  Tehachapi", Tehachapi_wind_df.index.min(), "..", Tehachapi_wind_df.index.max())


# Step 1 -- take a real copy of each slice.
# Each of the three is a *view* into weather_comp_df. Assigning to a column of a
# view triggers SettingWithCopyWarning and may not actually stick.
Fresno_solar_df = Fresno_solar_df.copy()
LA_urban_df = LA_urban_df.copy()
Tehachapi_wind_df = Tehachapi_wind_df.copy()


# Step 2 -- turn "time" from a string into a real timestamp.
# read_csv left it as text. utc=True reads the "+00:00" already in the file, so
# these stay in UTC and will line up with the EIA data later.
Fresno_solar_df["time"] = pd.to_datetime(Fresno_solar_df["time"], utc=True)
LA_urban_df["time"] = pd.to_datetime(LA_urban_df["time"], utc=True)
Tehachapi_wind_df["time"] = pd.to_datetime(Tehachapi_wind_df["time"], utc=True)


# Step 3 -- move "time" onto the index.
# This is the actual fix: now "row 5" means the same hour in all three frames,
# so concat has something meaningful to match on.
Fresno_solar_df = Fresno_solar_df.set_index("time")
LA_urban_df = LA_urban_df.set_index("time")
Tehachapi_wind_df = Tehachapi_wind_df.set_index("time")


# Step 4 -- drop the "site" column.
# Once the frame IS one site, the column just repeats the same string 14112
# times, and it would collide with the other two on concat.
Fresno_solar_df = Fresno_solar_df.drop(columns="site")
LA_urban_df = LA_urban_df.drop(columns="site")
Tehachapi_wind_df = Tehachapi_wind_df.drop(columns="site")


# Step 5 -- tag each site's columns with its name.
# All three frames have the same 8 column names. Duplicate column names are NOT
# an error in pandas -- df["temperature_2m"] would quietly hand back a 3-column
# DataFrame instead of a Series, and you'd only notice much later.
Fresno_solar_df = Fresno_solar_df.add_suffix("_fresno")
LA_urban_df = LA_urban_df.add_suffix("_la")
Tehachapi_wind_df = Tehachapi_wind_df.add_suffix("_tehachapi")


# Step 6 -- now the horizontal concat does what you want.
weather_matrix = pd.concat(
    [Fresno_solar_df, LA_urban_df, Tehachapi_wind_df],
    axis=1,
)

print("\nweather_matrix shape:", weather_matrix.shape)
print(weather_matrix.head(3).iloc[:, :4])

weather_matrix.to_csv("data/weather/multi_region_weather_data.csv")
# Step 7 -- check it actually worked.
# 14112 rows (not 42336), no NaN from bad alignment, no repeated column names.
print("\nrows          :", weather_matrix.shape[0], "(want 14112)")
print("NaN cells     :", int(weather_matrix.isna().sum().sum()), "(want 0)")
print("duplicate cols:", int(weather_matrix.columns.duplicated().sum()), "(want 0)")
print("index tz      :", weather_matrix.index.tz)
print("\ncolumns:", list(weather_matrix.columns))

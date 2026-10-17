#https://open-meteo.com/en/docs/historical-weather-api?latitude=31.024&longitude=-103.6616&start_date=2016-06-01&end_date=2016-06-30&hourly=temperature_2m,wind_speed_10m,wind_speed_100m,wind_direction_10m,wind_direction_100m,wind_gusts_10m,pressure_msl,surface_pressure&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch

#pip install openmeteo-requests
#pip install requests-cache retry-requests numpy pandas

from math import sqrt
from pathlib import Path

import openmeteo_requests

import pandas as pd
import requests_cache
from retry_requests import retry

# Setup the Open-Meteo API client with cache and retry on error
cache_session = requests_cache.CachedSession(".cache", expire_after=-1)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)

url = "https://archive-api.open-meteo.com/v1/archive"
local_timezone = "America/Chicago"
number_of_parameters = 3  # Set this to the number of weather parameters.
if number_of_parameters <= 0:
    raise ValueError("number_of_parameters must be greater than zero.")
params = {
    "latitude": 31.024,
    "longitude": -103.6616,
    "timezone": local_timezone,
    "hourly": [
        "temperature_2m",
        "wind_speed_10m",
        "wind_speed_100m",
        "wind_direction_10m",
        "wind_direction_100m",
        "wind_gusts_10m",
        "pressure_msl",
        "surface_pressure",
    ],
    "temperature_unit": "fahrenheit",
    "wind_speed_unit": "mph",
    "precipitation_unit": "inch",
}

hourly_dataframes = []
for year in range(2016, 2027):
    year_params = {
        **params,
        "start_date": f"{year}-06-01",
        "end_date": f"{year}-06-30",
    }
    response = openmeteo.weather_api(url, params=year_params)[0]

    hourly = response.Hourly()
    hourly_data = {
        "date": pd.date_range(
            start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
            end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left",
        ).tz_convert(local_timezone),
        "temperature_2m": hourly.Variables(0).ValuesAsNumpy(),
        "wind_speed_10m": hourly.Variables(1).ValuesAsNumpy(),
        "wind_speed_100m": hourly.Variables(2).ValuesAsNumpy(),
        "wind_direction_10m": hourly.Variables(3).ValuesAsNumpy(),
        "wind_direction_100m": hourly.Variables(4).ValuesAsNumpy(),
        "wind_gusts_10m": hourly.Variables(5).ValuesAsNumpy(),
        "pressure_msl": hourly.Variables(6).ValuesAsNumpy(),
        "surface_pressure": hourly.Variables(7).ValuesAsNumpy(),
    }
    hourly_dataframes.append(pd.DataFrame(data=hourly_data))

hourly_dataframe = pd.concat(hourly_dataframes, ignore_index=True)

# Convert the default pressure unit, hPa, to inHg.
hectopascals_per_inhg = 33.8638866667
pressure_columns = ["pressure_msl", "surface_pressure"]
hourly_dataframe[pressure_columns] = (
    hourly_dataframe[pressure_columns] / hectopascals_per_inhg
)

output_directory = Path(__file__).resolve().parent / "hourly_datasets"
output_directory.mkdir(parents=True, exist_ok=True)
summary_directory = output_directory / "sigma_summaries"
summary_directory.mkdir(parents=True, exist_ok=True)

parameter_columns = [
    "temperature_2m",
    "wind_speed_10m",
    "wind_speed_100m",
    "pressure_msl",
    "surface_pressure",
]
sigma_denominator = sqrt(number_of_parameters)
sigma_labels = {
    -3: f"-3/sqrt({number_of_parameters})sigma",
    -2: f"-2/sqrt({number_of_parameters})sigma",
    -1: f"-1/sqrt({number_of_parameters})sigma",
    0: "average",
    1: f"+1/sqrt({number_of_parameters})sigma",
    2: f"+2/sqrt({number_of_parameters})sigma",
    3: f"+3/sqrt({number_of_parameters})sigma",
}

hourly_summaries = []
for hour in range(9, 18):
    hourly_dataset = hourly_dataframe[hourly_dataframe["date"].dt.hour == hour]
    means = hourly_dataset[parameter_columns].mean()
    standard_deviations = hourly_dataset[parameter_columns].std()
    sigma_summary = pd.DataFrame({"parameter": parameter_columns})

    for sigma_level, label in sigma_labels.items():
        sigma_summary[label] = (
            means.to_numpy()
            + (sigma_level / sigma_denominator)
            * standard_deviations.to_numpy()
        )

    hourly_summaries.append(sigma_summary)

average_sigma_summary = (
    pd.concat(hourly_summaries)
    .groupby("parameter", sort=False)[list(sigma_labels.values())]
    .mean()
    .reset_index()
)

average_summary_path = summary_directory / "hours_09_to_17_sigma_summary.csv"
average_sigma_summary.to_csv(average_summary_path, index=False)

print(f"\nAverage across hours 09:00-17:00 local time ({local_timezone})")
print(
    average_sigma_summary.to_string(
        index=False,
        float_format=lambda value: f"{value:.3f}",
    )
)

import numpy as np
import matplotlib.pyplot as plt

# Replace with your measured wind speeds [m/s]
V10 = 15.489
V100 = 19.449

# Calculate power-law exponent
alpha = np.log(V100 / V10) / np.log(328 / 32.8)

# Altitudes from 0 ft to 30000 ft
altitude = np.arange(0, 35001, 5000)

# Estimate wind speed
wind_speed = V10 * (altitude / 32.8) ** alpha
wind_speed[0] = V10

print()
for altitude_ft, speed in zip(altitude, wind_speed):
    print(f"Altitude: {altitude_ft} ft, Wind speed: {speed:.2f} m/s")

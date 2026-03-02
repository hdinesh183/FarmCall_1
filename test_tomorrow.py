import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()
# Hardcoding just for the quick test to be 100% sure
api_key = "apjl2yikH8XILl15DNPqwjDQRlh0uxjl"

# Real-time and 1d, 1h timelines
url = f"https://api.tomorrow.io/v4/timelines?location=13.0,78.0&fields=temperature,temperatureMax,temperatureMin,precipitationProbability,windSpeed,rainAccumulation&timesteps=1h,1d&timezone=Asia/Kolkata&apikey={api_key}"
headers = {"accept": "application/json"}
response = requests.get(url, headers=headers)

with open("tomorrow.json", "w") as f:
    json.dump(response.json(), f, indent=2)

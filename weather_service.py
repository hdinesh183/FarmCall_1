import requests
from config import TOMORROW_IO_API_KEY
from database import SessionLocal
from models import WeatherData


def fetch_weekly_forecast(lat, lon):
    
    # Using the Tomorrow.io Premium API
    url = f"https://api.tomorrow.io/v4/timelines"
    params = {
        "location": f"{lat},{lon}",
        "fields": ["temperatureMax", "temperatureMin", "rainAccumulation", "precipitationProbability", "windSpeed", "temperature"],
        "timesteps": ["1h", "1d"],
        "units": "metric",
        "timezone": "Asia/Kolkata",
        "apikey": TOMORROW_IO_API_KEY
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        raise Exception(f"Weather API Error: {response.status_code} - {response.text}")
    
    raw = response.json()
    
    timelines = raw.get("data", {}).get("timelines", [])
    if not timelines:
        raise Exception("Invalid API response from Tomorrow.io")

    daily_data = []
    hourly_data = []

    for tl in timelines:
        if tl.get("timestep") == "1d":
            daily_data = tl.get("intervals", [])
        elif tl.get("timestep") == "1h":
            hourly_data = tl.get("intervals", [])
            
    mock_forecast = []
    
    for i in range(min(7, len(daily_data))):
        day = daily_data[i]
        date_str = day.get("startTime", "")[:10]  # Get YYYY-MM-DD
        vals = day.get("values", {})
        
        # Extract hourly slices specifically for that day (24 hours per day)
        hourly_slice = []
        if i == 0:
            for h_interval in hourly_data[:24]:
                h_vals = h_interval.get("values", {})
                time_str = h_interval.get("startTime", "")[11:16] # HH:MM
                hourly_slice.append({
                    "time": time_str,
                    "rain_probability": h_vals.get("precipitationProbability", 0),
                    "temperature": h_vals.get("temperature", 0)
                })

        mock_forecast.append({
            "date": date_str,
            "rain_probability": vals.get("precipitationProbability", 0),
            "rain_mm": vals.get("rainAccumulation", 0),
            "min_temp": vals.get("temperatureMin", 0),
            "max_temp": vals.get("temperatureMax", 0),
            "wind_speed": vals.get("windSpeed", 0),
            "hourly": hourly_slice
        })

    return {"forecast": mock_forecast}

def process_weekly_data(raw_data):

    daily_forecast = []

    for day in raw_data["forecast"]:
        daily_forecast.append({
            "date": day["date"],
            "rain_probability": day.get("rain_probability", 0),
            "rain_mm": day.get("rain_mm", 0),
            "min_temp": day.get("min_temp", 0),
            "max_temp": day.get("max_temp", 0),
            "wind_speed": day.get("wind_speed", 0),
            "hourly": day.get("hourly", [])
        })

    return daily_forecast

def store_weekly_forecast(village_id, forecast_data):

    db = SessionLocal()

    for day in forecast_data:
        weather = WeatherData(
            village_id=village_id,
            forecast_date=day["date"],
            rain_probability=day["rain_probability"],
            rain_mm=day["rain_mm"],
            min_temperature=day["min_temp"],
            max_temperature=day["max_temp"],
            wind_speed=day["wind_speed"]
        )

        db.add(weather)

    db.commit()
    db.close()

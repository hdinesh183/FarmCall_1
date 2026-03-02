from database import SessionLocal
from models import WeatherData
from datetime import date, timedelta


def analyze_weekly_risk(village_id):

    db = SessionLocal()

    today = date.today()
    next_week = today + timedelta(days=7)

    weekly_data = db.query(WeatherData).filter(
        WeatherData.village_id == village_id,
        WeatherData.forecast_date >= today,
        WeatherData.forecast_date <= next_week
    ).order_by(WeatherData.forecast_date).all()

    db.close()

    if not weekly_data:
        return {"risk_types": [], "risk_level": "LOW"}

    heavy_rain_days = 0
    continuous_rain_streak = 0
    max_rain_streak = 0
    dry_streak = 0
    max_dry_streak = 0
    heatwave_days = 0
    strong_wind_days = 0

    for day in weekly_data:

        # Heavy rain threshold aligned with farming rules
        if day.rain_mm is not None and day.rain_mm >= 30:
            heavy_rain_days += 1

        # Continuous rain detection (> 2.5 mm)
        if day.rain_mm is not None and day.rain_mm >= 2.5:
            continuous_rain_streak += 1
            dry_streak = 0
        else:
            max_rain_streak = max(max_rain_streak, continuous_rain_streak)
            continuous_rain_streak = 0
            dry_streak += 1

        max_dry_streak = max(max_dry_streak, dry_streak)

        # Heatwave detection
        if day.max_temperature is not None and day.max_temperature >= 40:
            heatwave_days += 1

        # Strong wind detection
        if day.wind_speed is not None and day.wind_speed >= 35:
            strong_wind_days += 1

    max_rain_streak = max(max_rain_streak, continuous_rain_streak)

    risk_types = []
    risk_score = 0

    # Heavy rain risk
    if heavy_rain_days >= 1:
        risk_types.append("heavy_rain")
        risk_score += 3

    # Continuous rain risk
    if max_rain_streak >= 3:
        risk_types.append("continuous_rain")
        risk_score += 3

    # Extended dry spell
    if max_dry_streak >= 4:
        risk_types.append("dry_spell")
        risk_score += 2

    # Heatwave risk
    if heatwave_days >= 2:
        risk_types.append("heatwave")
        risk_score += 2

    # Strong wind risk
    if strong_wind_days >= 2:
        risk_types.append("strong_wind")
        risk_score += 2

    # Final risk level logic
    if risk_score >= 5:
        risk_level = "HIGH"
    elif risk_score >= 3:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "risk_types": risk_types,
        "risk_level": risk_level,
        "risk_score": risk_score
    }


def should_trigger_call(risk_output):
    # Trigger only for HIGH risk
    return risk_output["risk_level"] == "HIGH"

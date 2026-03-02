from apscheduler.schedulers.background import BackgroundScheduler
from weather_service import fetch_weekly_forecast, process_weekly_data, store_weekly_forecast
from risk_engine import analyze_weekly_risk
from ai_advisory import generate_ai_advisory
from voice_service import generate_voice_file
from config import NGROK_URL
from call_service import make_twilio_call
from database import SessionLocal
import time
from models import Village, Advisory, Farmer
from datetime import date, timedelta


def run_daily_alert_pipeline():

    db = SessionLocal()
    villages = db.query(Village).all()

    for village in villages:

        # 1️⃣ Fetch Weather
        raw = fetch_weekly_forecast(village.latitude, village.longitude)
        processed = process_weekly_data(raw)
        store_weekly_forecast(village.id, processed)

        # 2️⃣ Analyze Risk
        risk_output = analyze_weekly_risk(village.id)

        if risk_output["risk_level"] == "HIGH":

            today_data = processed[0]
            tomorrow_data = processed[1] if len(processed) > 1 else today_data
            
            # Simple logic to determine weather descriptions based on API parameters
            rain_prob_today = today_data.get("rain_probability", 0)
            max_temp_today = today_data.get("max_temp", 0)
            
            today_weather = "Rainy" if rain_prob_today >= 50 else ("Hot and Sunny" if max_temp_today >= 35 else "Clear/Cloudy")
            rain_next_5_hours = "Yes" if rain_prob_today >= 50 else "No"
            tomorrow_rain = "Yes" if tomorrow_data.get("rain_probability", 0) >= 50 else "No"
            sun_condition = "Strong Sun" if max_temp_today >= 38 else ("Moderate Sun" if max_temp_today >= 30 else "Mild Sun")

            weekly_conditions = []
            for d in processed:
                desc = f"{d['date']}: "
                desc += "Rainy " if d.get('rain_probability', 0) >= 50 else "Dry "
                desc += f"Max {d.get('max_temp', 0)}C"
                weekly_conditions.append(desc)

            # Summarize the next 12 hours from the mock data
            next_12_hours = []
            for h in today_data.get('hourly', [])[:12]:
                next_12_hours.append(f"{h['time']}: Temp {h['temperature']}C, Rain {h['rain_probability']}%")

            # 3️⃣ Prepare structured input for AI
            weather_input = {
                "weekly_forecast": [day["rain_mm"] for day in processed],
                "weekly_conditions": ", ".join(weekly_conditions),
                "next_12_hours": ", ".join(next_12_hours),
                "today_weather": f"{today_weather} (Min: {today_data.get('min_temp', 0)}°C, Max: {max_temp_today}°C)",
                "rain_next_5_hours": rain_next_5_hours,
                "tomorrow_rain": tomorrow_rain,
                "sun_condition": sun_condition
            }

            # To avoid sending generic messages, group farmers by their preferred language
            farmers = db.query(Farmer).filter(Farmer.village_id == village.id).all()
            
            # Group by language
            lang_groups = {}
            for f in farmers:
                lang = f.language or "English"
                if lang not in lang_groups:
                    lang_groups[lang] = []
                lang_groups[lang].append(f)
                
            for lang, f_list in lang_groups.items():
                
                # 4️⃣ Generate Localized AI Advisory
                advisory_text = generate_ai_advisory(village.village_name, weather_input, language=lang)

                # 4.5️⃣ Convert Advisory to Voice in local language
                audio_file = generate_voice_file(advisory_text, language=lang)

                # 5️⃣ Store Advisory
                advisory = Advisory(
                    village_id=village.id,
                    forecast_start_date=date.today(),
                    forecast_end_date=date.today() + timedelta(days=7),
                    risk_level=risk_output["risk_level"],
                    risk_type=",".join(risk_output["risk_types"]),
                    advisory_text=advisory_text,
                    audio_filename=audio_file,
                    language=lang,
                    trigger_type="auto"
                )

                db.add(advisory)
                db.commit()

                # 6️⃣ Trigger Voice Calls (Concurrently)
                from concurrent.futures import ThreadPoolExecutor
                
                def fire_call(f, lang_code):
                    if audio_file.startswith("http"):
                        audio_url = audio_file
                    else:
                        audio_url = f"{NGROK_URL}/audio_files/{audio_file}"
                    
                    try:
                        make_twilio_call(f.phone, audio_url, language=lang_code)
                    except Exception as e:
                        print(f"Failed to call {f.phone}: {e}")

                with ThreadPoolExecutor(max_workers=10) as executor:
                    for f in f_list:
                        executor.submit(fire_call, f, lang)

                # Respect API limits by sleeping 10 seconds between AI generations
                time.sleep(10)

    db.close()
    
def start_scheduler():
    scheduler = BackgroundScheduler()
    # Run every morning at 7:00 AM
    scheduler.add_job(run_daily_alert_pipeline, 'cron', hour=7, minute=0)
    scheduler.start()
    print("Scheduler started. Risk checks will run daily at 7:00 AM.")

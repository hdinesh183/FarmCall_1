# python -m uvicorn main:app --port 8000 --reload
# ./ngrok.exe http --url=lachrymal-leatha-tuskless.ngrok-free.dev 8000
# http://127.0.0.1:8000/run-daily-alerts


# psql -U postgres -d farmcall_db

from fastapi import FastAPI, BackgroundTasks, HTTPException
from risk_engine import analyze_weekly_risk, should_trigger_call
from ai_advisory import generate_ai_advisory
from scheduler import start_scheduler, run_daily_alert_pipeline
from weather_service import fetch_weekly_forecast, process_weekly_data, store_weekly_forecast
from voice_service import generate_voice_file
from call_service import make_twilio_call
from config import NGROK_URL
from models import Village, Farmer, Advisory, WeatherData, AdvisoryCall
from database import SessionLocal, engine, Base
import os
import asyncio
from datetime import date, timedelta
from pydantic import BaseModel
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import requests

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup event: Launch the scheduled daily checks
    Base.metadata.create_all(bind=engine)
    start_scheduler()
    yield
    # Shutdown event: can add cleanup here

app = FastAPI(title="Farmcall API", lifespan=lifespan)

from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    print(f"Global Exception: {exc}")
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"status": "error", "message": f"Server Crash: {str(exc)}"})

os.makedirs("audio_files", exist_ok=True)
app.mount("/audio_files", StaticFiles(directory="audio_files"), name="audio_files")

# Ensure static dir exists for index.html
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")

@app.get("/check-risk/{village_id}")
def check_risk(village_id: int):
    risk = analyze_weekly_risk(village_id)
    
    if should_trigger_call(risk):
        action = "Trigger AI advisory + Call"
    else:
        action = "No call required"
        
    return {
        "status": "success",
        "action": action,
        "risk_details": risk
    }

@app.get("/generate-advisory-test")
def generate_test():
    sample_weather = {
        "weekly_forecast": [0, 5, 12, 35, 40, 10, 0],
        "today_weather": "Cloudy",
        "rain_next_5_hours": "Yes",
        "tomorrow_rain": "Yes",
        "sun_condition": "Light sun"
    }

    advisory = generate_ai_advisory(sample_weather)

    return {"advisory": advisory}

@app.get("/update-weather")
def update_weather():

    db = SessionLocal()
    villages = db.query(Village).all()

    for village in villages:
        raw = fetch_weekly_forecast(village.latitude, village.longitude)
        processed = process_weekly_data(raw)
        store_weekly_forecast(village.id, processed)

    db.close()

    return {"message": "Weekly forecast updated successfully"}

async def cleanup_audio_files(delay_seconds: int = 300):
    """
    Waits for Twilio to finish playing the audio (e.g. 5 minutes),
    then deletes all generated .mp3 files and associated database records.
    """
    await asyncio.sleep(delay_seconds)
    audio_dir = "audio_files"
    if os.path.exists(audio_dir):
        for filename in os.listdir(audio_dir):
            if filename.endswith(".mp3"):
                file_path = os.path.join(audio_dir, filename)
                try:
                    os.remove(file_path)
                    print(f"Auto-deleted: {filename}")
                except Exception as e:
                    print(f"Failed to delete {filename}: {e}")

    try:
        db = SessionLocal()
        db.query(AdvisoryCall).delete()
        db.query(Advisory).delete()
        db.query(WeatherData).delete()
        db.commit()
        db.close()
        print("Auto-deleted temporal database records (weather_data, advisories, advisory_calls).")
    except Exception as e:
        print(f"Failed to delete temporal database records: {e}")

@app.get("/run-daily-alerts")
def run_alerts(background_tasks: BackgroundTasks):
    run_daily_alert_pipeline()
    
    # Schedule the cleanup task to run 5 minutes (300s) after the pipeline finishes
    background_tasks.add_task(cleanup_audio_files, 300)
    
    return {"message": "Daily alert pipeline executed. Audio files will be auto-deleted in 5 minutes after calls complete."}

class FarmerCreate(BaseModel):
    name: str
    phone: str
    village_id: int
    language: str = "English"

@app.post("/add-farmer")
def add_farmer(farmer: FarmerCreate):
    db = SessionLocal()
    
    # Check if village exists
    village = db.query(Village).filter(Village.id == farmer.village_id).first()
    if not village:
        db.close()
        return {"error": "Village ID does not exist"}
        
    # Check if phone already exists
    existing = db.query(Farmer).filter(Farmer.phone == farmer.phone).first()
    if existing:
        db.close()
        return {"error": "Phone number already registered"}

    new_farmer = Farmer(
        name=farmer.name,
        phone=farmer.phone,
        village_id=farmer.village_id,
        language=farmer.language
    )
    db.add(new_farmer)
    db.commit()
    db.close()

    return {"message": f"Farmer {farmer.name} added successfully!", "phone": farmer.phone}

class VillageCallRequest(BaseModel):
    village_name: str

@app.post("/api/call-village")
def call_village_endpoint(req: VillageCallRequest, background_tasks: BackgroundTasks):
    db = SessionLocal()
    village = db.query(Village).filter(Village.village_name.ilike(req.village_name)).first()
    if not village:
        db.close()
        raise HTTPException(status_code=404, detail="Village not found")
        
    farmers = db.query(Farmer).filter(Farmer.village_id == village.id).all()
    if not farmers:
        db.close()
        raise HTTPException(status_code=404, detail="No farmers connected to this village")

    lang_groups = {}
    for f in farmers:
        lang = f.language or "English"
        if lang not in lang_groups:
            lang_groups[lang] = []
        lang_groups[lang].append(f)

    # 2. Trigger the call for this village
    try:
        raw = fetch_weekly_forecast(village.latitude, village.longitude)
        processed = process_weekly_data(raw)
        store_weekly_forecast(village.id, processed)

        today_data = processed[0]
        tomorrow_data = processed[1] if len(processed) > 1 else today_data
        
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

        next_12_hours = []
        for h in today_data.get('hourly', [])[:12]:
            next_12_hours.append(f"{h['time']}: Temp {h['temperature']}C, Rain {h['rain_probability']}%")

        weather_input = {
            "weekly_forecast": [day["rain_mm"] for day in processed],
            "weekly_conditions": ", ".join(weekly_conditions),
            "next_12_hours": ", ".join(next_12_hours),
            "today_weather": f"{today_weather} (Min: {today_data.get('min_temp', 0)}°C, Max: {max_temp_today}°C)",
            "rain_next_5_hours": rain_next_5_hours,
            "tomorrow_rain": tomorrow_rain,
            "sun_condition": sun_condition
        }

        # Concurrently call all farmers
        from concurrent.futures import ThreadPoolExecutor

        def fire_call(f, audio_url, lang):
            try:
                make_twilio_call(f.phone, audio_url, language=lang)
            except Exception as e:
                print(f"Failed to call {f.phone}: {e}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            for lang, f_list in lang_groups.items():
                advisory_text = generate_ai_advisory(village.village_name, weather_input, language=lang)
                audio_file = generate_voice_file(advisory_text, language=lang)
                
                advisory = Advisory(
                    village_id=village.id,
                    forecast_start_date=date.today(),
                    forecast_end_date=date.today() + timedelta(days=7),
                    risk_level="MANUAL_VILLAGE", # This was not part of the instruction, but was in the provided diff. Keeping it as is.
                    risk_type="Village_Trigger", # This was not part of the instruction, but was in the provided diff. Keeping it as is.
                    advisory_text=advisory_text,
                    audio_filename=audio_file,
                    language=lang,
                    trigger_type="manual" # This was not part of the instruction, but was in the provided diff. Keeping it as is.
                )
                db.add(advisory)
                db.commit()

                if audio_file.startswith("http"):
                    audio_url = audio_file
                else:
                    audio_url = f"{NGROK_URL}/audio_files/{audio_file}"
                
                for f in f_list:
                    executor.submit(fire_call, f, audio_url, lang)

        background_tasks.add_task(cleanup_audio_files, 300)

        v_name = village.village_name
        db.close()
        return {"status": "success", "message": f"Successfully advised and called {len(farmers)} farmer(s) in {v_name}."}

    except Exception as e:
        db.close()
        raise HTTPException(status_code=500, detail=str(e))

class RegisterCallRequest(BaseModel):
    name: str
    phone: str
    village_name: str
    mandal: str = "Unknown"
    district: str = "Unknown"
    state: str = "Unknown"
    language: str = "English"
    crop: str = "None"

@app.post("/api/register-and-call")
def register_and_call(req: RegisterCallRequest, background_tasks: BackgroundTasks):
    db = SessionLocal()

    # 1. Check if phone exists
    farmer = db.query(Farmer).filter(Farmer.phone == req.phone).first()
    
    if not farmer:
        # Geocode the location
        lat, lon = None, None
        try:
            # OpenStreetMap is very strict about User-Agents; use a standard browser spoof to prevent 403 Forbidden
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            # Be generous with location parameters
            q = f"{req.village_name}, {req.mandal}, {req.district}, {req.state}"
            url = f"https://nominatim.openstreetmap.org/search"
            params = {"q": q, "format": "json", "limit": 1}
            
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                if data:
                    lat = float(data[0]['lat'])
                    lon = float(data[0]['lon'])
        except Exception as e:
            print(f"Geocoding failed: {e}")

        if lat is None or lon is None:
            # Check just the village name string as fallback
            try:
                url = f"https://nominatim.openstreetmap.org/search"
                params = {"q": req.village_name, "format": "json", "limit": 1}
                response = requests.get(url, headers=headers, params=params)
                if response.status_code == 200 and response.json():
                    data = response.json()
                    lat = float(data[0]['lat'])
                    lon = float(data[0]['lon'])
                else:
                    print(f"Fallback Geocoding failed: Location {req.village_name} not found.")
                    lat, lon = 0.0, 0.0
            except Exception as e:
                print(f"Geocoding API completely failed: {e}")
                lat, lon = 0.0, 0.0

        village = db.query(Village).filter(Village.village_name == req.village_name).first()
        if not village:
            village = Village(
                village_name=req.village_name,
                mandal=req.mandal,
                district=req.district,
                state=req.state,
                latitude=lat,
                longitude=lon
            )
            db.add(village)
            db.commit()
            db.refresh(village)
        
        farmer = Farmer(
            name=req.name,
            phone=req.phone,
            village_id=village.id,
            language=req.language,
            crop=req.crop
        )
        db.add(farmer)
        db.commit()
        db.refresh(farmer)

    village = farmer.village
    if not village:
        village = db.query(Village).filter(Village.id == farmer.village_id).first()

    # 2. Trigger the call for this farmer
    try:
        raw = fetch_weekly_forecast(village.latitude, village.longitude)
        processed = process_weekly_data(raw)
        store_weekly_forecast(village.id, processed)

        today_data = processed[0]
        tomorrow_data = processed[1] if len(processed) > 1 else today_data
        
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

        next_12_hours = []
        for h in today_data.get('hourly', [])[:12]:
            next_12_hours.append(f"{h['time']}: Temp {h['temperature']}C, Rain {h['rain_probability']}%")

        weather_input = {
            "weekly_forecast": [day["rain_mm"] for day in processed],
            "weekly_conditions": ", ".join(weekly_conditions),
            "next_12_hours": ", ".join(next_12_hours),
            "today_weather": f"{today_weather} (Min: {today_data.get('min_temp', 0)}°C, Max: {max_temp_today}°C)",
            "rain_next_5_hours": rain_next_5_hours,
            "tomorrow_rain": tomorrow_rain,
            "sun_condition": sun_condition
        }

        advisory_text = generate_ai_advisory(village.village_name, weather_input, language=farmer.language)
        audio_file = generate_voice_file(advisory_text, language=farmer.language)
        
        advisory = Advisory(
            village_id=village.id,
            forecast_start_date=date.today(),
            forecast_end_date=date.today() + timedelta(days=7),
            risk_level="HIGH",
            risk_type="Onboarding_Trigger",
            advisory_text=advisory_text,
            audio_filename=audio_file,
            language=farmer.language,
            trigger_type="manual"
        )
        db.add(advisory)
        db.commit()

        if audio_file.startswith("http"):
            audio_url = audio_file
        else:
            audio_url = f"{NGROK_URL}/audio_files/{audio_file}"

        make_twilio_call(farmer.phone, audio_url, language=farmer.language)
        
        background_tasks.add_task(cleanup_audio_files, 300)

        db.close()
        return {"status": "success", "message": f"Successfully onboarded and dialed {farmer.name} at {farmer.phone}."}

    except Exception as e:
        db.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/demo-call")
def demo_call(req: RegisterCallRequest, background_tasks: BackgroundTasks):
    
    # Geocode the location
    lat, lon = None, None
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        q = f"{req.village_name}, {req.mandal}, {req.district}, {req.state}"
        url = f"https://nominatim.openstreetmap.org/search"
        params = {"q": q, "format": "json", "limit": 1}
        
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            if data:
                lat = float(data[0]['lat'])
                lon = float(data[0]['lon'])
    except Exception as e:
        print(f"Geocoding failed: {e}")

    if lat is None or lon is None:
        try:
            url = f"https://nominatim.openstreetmap.org/search"
            params = {"q": req.village_name, "format": "json", "limit": 1}
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200 and response.json():
                data = response.json()
                lat = float(data[0]['lat'])
                lon = float(data[0]['lon'])
            else:
                lat, lon = 0.0, 0.0
        except Exception as e:
            lat, lon = 0.0, 0.0

    try:
        raw = fetch_weekly_forecast(lat, lon)
        processed = process_weekly_data(raw)

        today_data = processed[0]
        tomorrow_data = processed[1] if len(processed) > 1 else today_data
        
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

        next_12_hours = []
        for h in today_data.get('hourly', [])[:12]:
            next_12_hours.append(f"{h['time']}: Temp {h['temperature']}C, Rain {h['rain_probability']}%")

        weather_input = {
            "weekly_forecast": [day["rain_mm"] for day in processed],
            "weekly_conditions": ", ".join(weekly_conditions),
            "next_12_hours": ", ".join(next_12_hours),
            "today_weather": f"{today_weather} (Min: {today_data.get('min_temp', 0)}°C, Max: {max_temp_today}°C)",
            "rain_next_5_hours": rain_next_5_hours,
            "tomorrow_rain": tomorrow_rain,
            "sun_condition": sun_condition
        }

        # Stateless tracking: NO database entries
        advisory_text = generate_ai_advisory(req.village_name, weather_input, language=req.language)
        audio_file = generate_voice_file(advisory_text, language=req.language)
        
        if audio_file.startswith("http"):
            audio_url = audio_file
        else:
            audio_url = f"{NGROK_URL}/audio_files/{audio_file}"

        make_twilio_call(req.phone, audio_url, language=req.language)
        
        background_tasks.add_task(cleanup_audio_files, 300)

        return {"status": "success", "message": f"Demo Triggered! Dialing {req.phone}..."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/send-alert")
def admin_send_alert(village_id: int, message: str, background_tasks: BackgroundTasks):

    db = SessionLocal()

    village = db.query(Village).filter(Village.id == village_id).first()

    if not village:
        db.close()
        raise HTTPException(status_code=404, detail="Village not found")

    # 1️⃣ Store advisory
    advisory = Advisory(
        village_id=village.id,
        forecast_start_date=date.today(),
        forecast_end_date=date.today() + timedelta(days=7),
        risk_level="MANUAL",
        risk_type="MANUAL_OVERRIDE",
        advisory_text=message,
        language="English",
        trigger_type="manual"
    )

    db.add(advisory)
    db.commit()

    # 2️⃣ Generate Voice
    filename = generate_voice_file(message, "English")

    if filename.startswith("http"):
        audio_url = filename
    else:
        audio_url = f"{NGROK_URL}/audio_files/{filename}"

    # 3️⃣ Call all farmers in that village concurrently
    farmers = db.query(Farmer).filter(Farmer.village_id == village.id).all()

    from concurrent.futures import ThreadPoolExecutor
    
    def fire_call(f):
        try:
            make_twilio_call(f.phone, audio_url, language="English")
        except Exception as e:
            print(f"Failed to call {f.phone}: {e}")

    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(fire_call, farmers)

    db.close()
    
    # Schedule the cleanup task to run 5 minutes (300s) after the manual alerts finish
    background_tasks.add_task(cleanup_audio_files, 300)

    return {"message": "Manual alert sent successfully"}

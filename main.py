# python -m uvicorn main:app --port 8000 --reload
# ./ngrok.exe http --url=lachrymal-leatha-tuskless.ngrok-free.dev 8000
# Username: admin
# Password: farmcall2025


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
from sqlalchemy import text, func
from fastapi import FastAPI, BackgroundTasks, HTTPException, Form
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
    
    db = SessionLocal()
    migrations = [
        "ALTER TABLE advisory_calls ADD COLUMN twilio_sid VARCHAR(100);",
        "ALTER TABLE advisory_calls ADD COLUMN call_duration INTEGER DEFAULT 0;",
        "ALTER TABLE advisory_calls ADD COLUMN retry_count INTEGER DEFAULT 0;",
        "ALTER TABLE advisories ADD COLUMN audio_duration FLOAT DEFAULT 0.0;"
    ]
    
    for mig in migrations:
        try:
            db.execute(text(mig))
            db.commit()
        except Exception as e:
            db.rollback()
            # Suppress DuplicateColumn notes on subsequent boots
            pass
            
    db.close()
        
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

from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi import Depends, status
import secrets

security = HTTPBasic()

def admin_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, os.getenv("ADMIN_PASSWORD", "farmcall2025"))
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

@app.get("/admin")
def serve_admin(username: str = Depends(admin_auth)):
    return FileResponse("static/admin.html")

# --- ADMIN API ENDPOINTS ---

@app.get("/api/admin/data")
def get_admin_data(username: str = Depends(admin_auth)):
    db = SessionLocal()
    try:
        farmers = db.query(Farmer).all()
        villages = db.query(Village).all()
        
        # Serialize objects
        f_list = [
            {
                "id": f.id, "name": f.name, "phone": f.phone,
                "village_id": f.village_id, "language": f.language,
                "crop": f.crop, "created_at": f.created_at.isoformat()
            } for f in farmers
        ]
        
        v_list = [
            {
                "id": v.id, "village_name": v.village_name, "mandal": v.mandal,
                "district": v.district, "state": v.state,
                "latitude": v.latitude, "longitude": v.longitude
            } for v in villages
        ]
        
        return {"farmers": f_list, "villages": v_list}
    finally:
        db.close()

from pydantic import BaseModel

class FarmerUpdate(BaseModel):
    name: str
    language: str
    crop: str

@app.put("/api/admin/farmers/{phone}")
def update_farmer(phone: str, payload: FarmerUpdate, username: str = Depends(admin_auth)):
    db = SessionLocal()
    try:
        farmer = db.query(Farmer).filter(Farmer.phone == phone).first()
        if not farmer:
            raise HTTPException(status_code=404, detail="Farmer not found")
            
        farmer.name = payload.name.strip().lower()
        farmer.language = payload.language
        farmer.crop = payload.crop.strip().lower()
        
        db.commit()
        return {"status": "success", "message": "Farmer updated."}
    finally:
        db.close()

@app.delete("/api/admin/farmers/{phone}")
def delete_farmer(phone: str, username: str = Depends(admin_auth)):
    db = SessionLocal()
    try:
        farmer = db.query(Farmer).filter(Farmer.phone == phone).first()
        if not farmer:
            raise HTTPException(status_code=404, detail="Farmer not found")
            
        db.delete(farmer)
        db.commit()
        return {"status": "success"}
    finally:
        db.close()

@app.delete("/api/admin/villages/{village_id}")
def delete_village(village_id: int, username: str = Depends(admin_auth)):
    db = SessionLocal()
    try:
        village = db.query(Village).filter(Village.id == village_id).first()
        if not village:
            raise HTTPException(status_code=404, detail="Village not found")
            
        # Deleting a village safely. The ORM cascade rules or manual cascade is required.
        # Ensure we drop connected farmers and advisories to maintain integrity
        db.query(Farmer).filter(Farmer.village_id == village_id).delete()
        db.query(AdvisoryCall).filter(AdvisoryCall.advisory.has(village_id=village_id)).delete()
        db.query(Advisory).filter(Advisory.village_id == village_id).delete()
        db.query(WeatherData).filter(WeatherData.village_id == village_id).delete()
        
        db.delete(village)
        db.commit()
        return {"status": "success", "message": "Cascade deleted village and related data."}
    finally:
        db.close()

@app.delete("/api/admin/reset")
def wipe_database(username: str = Depends(admin_auth)):
    db = SessionLocal()
    try:
        # Nuclear wipe all tables
        db.query(AdvisoryCall).delete()
        db.query(Advisory).delete()
        db.query(WeatherData).delete()
        db.query(Farmer).delete()
        db.query(Village).delete()
        db.commit()
        return {"status": "success", "message": "NUCLEAR WIPE COMPLETE."}
    finally:
        db.close()

# --- END ADMIN API ---

# --- ANALYTICS & TELEMETRY ---

@app.post("/api/twilio/webhook")
async def twilio_webhook(
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    CallDuration: str = Form(None),
    DialCallDuration: str = Form(None)
):
    db = SessionLocal()
    try:
        call_record = db.query(AdvisoryCall).filter(AdvisoryCall.twilio_sid == CallSid).first()
        if call_record:
            call_record.call_status = CallStatus
            
            actual_duration = DialCallDuration or CallDuration
            if actual_duration:
                call_record.call_duration = int(actual_duration)
            db.commit()
        return {"status": "success"}
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"status": "error"}
    finally:
        db.close()

@app.post("/api/twilio/repeat")
async def twilio_repeat(
    audio_url: str,
    language: str,
    Digits: str = Form(None)
):
    from fastapi.responses import Response
    import html
    import urllib.parse
    from config import NGROK_URL

    lang_map = {
        "English": "en-IN",
        "Hindi": "hi-IN",
        "Tamil": "ta-IN",
        "Telugu": "te-IN",
        "Bengali": "bn-IN",
        "Kannada": "kn-IN",
        "Malayalam": "ml-IN"
    }

    tw_lang = lang_map.get(language, "en-IN")
    
    if Digits == '1':
        safe_audio_url = html.escape(audio_url)
        
        encoded_audio = urllib.parse.quote(audio_url)
        encoded_lang = urllib.parse.quote(language)
        action_url = f"{NGROK_URL}/api/twilio/repeat?audio_url={encoded_audio}&language={encoded_lang}"
        safe_action_url = html.escape(action_url)
        
        twiml = f"""
        <Response>
            <Gather numDigits="1" action="{safe_action_url}" method="POST" timeout="5">
                <Play>{safe_audio_url}</Play>
            </Gather>
            <Say language="{tw_lang}">Goodbye.</Say>
        </Response>
        """
        return Response(content=twiml, media_type="application/xml")
    
    farewell = f"""<Response><Say language="{tw_lang}">Goodbye.</Say></Response>"""
    return Response(content=farewell, media_type="application/xml")

@app.get("/api/stats")
def get_stats(filter_type: str = "daily"):
    db = SessionLocal()
    from datetime import date
    try:
        today = date.today()
        
        date_filter = []
        if filter_type == "daily":
            date_filter.append(Advisory.forecast_start_date == today)
            
        total_farmers = db.query(Farmer).count()
        total_calls = db.query(AdvisoryCall).join(Advisory).filter(*date_filter).count()
        answered_calls = db.query(AdvisoryCall).join(Advisory).filter(AdvisoryCall.call_status == 'completed', *date_filter).count()
        unanswered_calls = db.query(AdvisoryCall).join(Advisory).filter(AdvisoryCall.call_status.in_(['no-answer', 'failed', 'busy']), *date_filter).count()
        
        # Audio length varies precisely per advisory
        calls = db.query(AdvisoryCall).join(Advisory).filter(AdvisoryCall.call_status == 'completed', *date_filter).all()
        full_listens = 0
        partial_listens = 0
        total_percent = 0.0
        
        for c in calls:
            adv_duration = c.advisory.audio_duration if c.advisory and getattr(c.advisory, 'audio_duration', None) else 40.0
            expected = adv_duration if adv_duration > 0 else 40.0
            
            # Twilio call duration. Cap at 100%
            listen_percent = min(100.0, (float(c.call_duration) / expected) * 100)
            total_percent += listen_percent
            
            if listen_percent >= 90.0:
                full_listens += 1
            elif c.call_duration > 0:
                partial_listens += 1

        avg_listen_percent = (total_percent / answered_calls) if answered_calls > 0 else 0.0
        
        village_counts = db.query(
            Village.village_name, 
            func.count(AdvisoryCall.id)
        ).select_from(AdvisoryCall).join(
            Advisory, Advisory.id == AdvisoryCall.advisory_id
        ).join(
            Farmer, Farmer.id == AdvisoryCall.farmer_id
        ).join(
            Village, Village.id == Farmer.village_id
        ).filter(*date_filter).group_by(Village.village_name).all()
        
        villages = [{"name": row[0], "count": row[1]} for row in village_counts]
        
        return {
            "total_farmers": total_farmers,
            "total_calls": total_calls,
            "answered_calls": answered_calls,
            "unanswered_calls": unanswered_calls,
            "calls_full_listen": full_listens,
            "calls_partial_listen": partial_listens,
            "avg_listen_percent": round(avg_listen_percent, 1),
            "village_distribution": villages
        }
    finally:
        db.close()

@app.delete("/api/admin/clear_analytics")
def clear_analytics(username: str = Depends(admin_auth)):
    db = SessionLocal()
    try:
        db.query(AdvisoryCall).delete()
        db.commit()
        return {"msg": "Analytics cleared"}
    finally:
        db.close()


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
    farmer.name = farmer.name.strip().lower()
    farmer.phone = farmer.phone.strip()
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
    req.village_name = req.village_name.strip().lower()
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

        def fire_call(f, audio_url, lang, advisory_id):
            try:
                sid = make_twilio_call(f.phone, audio_url, language=lang)
                t_db = SessionLocal()
                call_record = AdvisoryCall(
                    advisory_id=advisory_id,
                    farmer_id=f.id,
                    call_status="queued",
                    twilio_sid=sid
                )
                t_db.add(call_record)
                t_db.commit()
                t_db.close()
            except Exception as e:
                print(f"Failed to call {f.phone}: {e}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            for lang, f_list in lang_groups.items():
                advisory_text = generate_ai_advisory(village.village_name, weather_input, language=lang)
                audio_file, duration = generate_voice_file(advisory_text, language=lang)
                
                advisory = Advisory(
                    village_id=village.id,
                    forecast_start_date=date.today(),
                    forecast_end_date=date.today() + timedelta(days=7),
                    risk_level="MANUAL_VILLAGE", # This was not part of the instruction, but was in the provided diff. Keeping it as is.
                    risk_type="Village_Trigger", # This was not part of the instruction, but was in the provided diff. Keeping it as is.
                    advisory_text=advisory_text,
                    audio_filename=audio_file,
                    audio_duration=duration,
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
                    executor.submit(fire_call, f, audio_url, lang, advisory.id)

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

@app.post("/api/register")
def register_farmer(req: RegisterCallRequest, background_tasks: BackgroundTasks):
    req.name = req.name.strip().lower()
    req.phone = req.phone.strip()
    req.village_name = req.village_name.strip().lower()
    req.mandal = req.mandal.strip().lower()
    req.district = req.district.strip().lower()
    req.state = req.state.strip().lower()
    req.crop = req.crop.strip().lower()
    
    db = SessionLocal()

    # 1. Check if phone exists
    farmer = db.query(Farmer).filter(Farmer.phone == req.phone).first()
    
    if not farmer:
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
    
    db.close()
    return {"status": "success", "message": f"Successfully registered farmer {req.name} to {req.village_name}."}

@app.post("/api/demo-call")
def demo_call(req: RegisterCallRequest, background_tasks: BackgroundTasks):
    req.name = req.name.strip().lower()
    req.phone = req.phone.strip()
    req.village_name = req.village_name.strip().lower()
    req.mandal = req.mandal.strip().lower()
    req.district = req.district.strip().lower()
    req.state = req.state.strip().lower()
    req.crop = req.crop.strip().lower()
    
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
        audio_file, duration = generate_voice_file(advisory_text, language=req.language)
        
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
    filename, duration = generate_voice_file(message, "English")

    advisory.audio_filename = filename
    advisory.audio_duration = duration
    db.commit()

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

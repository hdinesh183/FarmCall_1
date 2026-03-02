import requests
import os
import uuid
from config import MURF_API_KEY, MURF_API_URL


# Only include verified Murf voice IDs
# For languages not supported by Murf, we use English voice
# but Gemini still generates the advisory TEXT in the local language
voice_map = {
    "English": "en-IN-priya",   # Priya (Young Adult, Conversational)
    "Hindi": "hi-IN-shweta",    # Shweta (Middle-Aged, Calm/Conversational)
    "Tamil": "ta-IN-iniya",     # Iniya (Young Adult, Conversational)
    "Bengali": "bn-IN-anwesha", # Anwesha (Young Adult, Conversational)
    "Telugu": "en-IN-priya",    # Priya (Sweet/Conversational Fallback for Telugu)
    "Kannada": "kn-IN-shruti",
    "Malayalam": "en-IN-alia",  # Alia (Sweet/Conversational Fallback for Malayalam)
}

# Languages where Murf doesn't have a voice — fallback to English voice
FALLBACK_VOICE = "en-IN-isha"

def generate_voice_file(text, language="English"):

    voice_id = voice_map.get(language, FALLBACK_VOICE)

    payload = {
        "voiceId": voice_id,
        "text": text,
        "format": "mp3"
    }

    headers = {
        "api-key": MURF_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            MURF_API_URL,
            json=payload,
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            print(f"Murf API Error ({response.status_code}): {response.text}")
            print("Falling back to gTTS...")
            return _fallback_gtts(text, language)

        resp_json = response.json()
        audio_url = resp_json.get("audioFile")
        
        if not audio_url:
            print("No audioFile in Murf response. Falling back to gTTS...")
            return _fallback_gtts(text, language)
            
        print(f"Generated Murf URL: {audio_url}")
        # Instead of downloading through Ngrok, return the high-speed AWS CDN link directly to Twilio!
        return audio_url

    except Exception as e:
        print(f"Murf API request failed: {e}")
        print("Falling back to gTTS...")
        return _fallback_gtts(text, language)


def _fallback_gtts(text, language="English"):
    """Fallback to gTTS if Murf fails"""
    from gtts import gTTS

    lang_code_map = {
        "English": "en",
        "Hindi": "hi",
        "Telugu": "te",
        "Tamil": "ta",
        "Marathi": "mr",
        "Bengali": "bn",
        "Kannada": "kn",
        "Malayalam": "ml",
        "Gujarati": "gu",
        "Punjabi": "pa"
    }

    lang_code = lang_code_map.get(language, "en")

    os.makedirs("audio_files", exist_ok=True)
    filename = f"{uuid.uuid4()}.mp3"
    filepath = os.path.join("audio_files", filename)

    tts = gTTS(text=text, lang=lang_code)
    tts.save(filepath)

    return filename


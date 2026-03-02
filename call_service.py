from twilio.rest import Client
from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER


client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def make_twilio_call(phone_number, audio_url, language="English"):

    lang_map = {
        "English": ("en-IN", "Namaste. Farmcall is connecting your personalized weather alert. Please hold on."),
        "Hindi": ("hi-IN", "नमस्ते। फार्मकॉल आपकी मौसम की जानकारी कनेक्ट कर रहा है। कृपया लाइन पर बने रहें।"),
        "Tamil": ("ta-IN", "வணக்கம். ஃபார்ம்கால் உங்கள் வானிலை அறிக்கையை இணைக்கிறது. தயவுசெய்து காத்திருக்கவும்."),
        "Telugu": ("te-IN", "నమస్కారం. ఫార్మ్‌కాల్ మీ వాతావరణ సమాచారాన్ని కనెక్ట్ చేస్తోంది. దయచేసి వేచి ఉండండి."),
        "Bengali": ("bn-IN", "নমস্কার। ফার্মকল আপনার আবহাওয়ার খবর কানেক্ট করছে। অনুগ্রহ করে অপেক্ষা করুন।"),
        "Kannada": ("kn-IN", "ನಮಸ್ಕಾರ. ಫಾರ್ಮ್‌ಕಾಲ್ ನಿಮ್ಮ ಹವಾಮಾನ ವರದಿಯನ್ನು ಸಂಪರ್ಕಿಸುತ್ತಿದೆ. ದಯವಿಟ್ಟು ಕಾಯಿರಿ."),
        "Malayalam": ("ml-IN", "നമസ്കാരം. ഫാംകോൾ നിങ്ങളുടെ കാലാവസ്ഥാ അറിയിപ്പ് കണക്റ്റ് ചെയ്യുന്നു. ദയവായി കാത്തിരിക്കുക.")
    }

    tw_lang, tw_text = lang_map.get(language, lang_map["English"])
    
    import html
    safe_audio_url = html.escape(audio_url)

    twiml = f"""
    <Response>
        <Say language="{tw_lang}">{tw_text}</Say>
        <Play>{safe_audio_url}</Play>
    </Response>
    """

    call = client.calls.create(
        twiml=twiml,
        to=phone_number,
        from_=TWILIO_PHONE_NUMBER
    )

    return call.sid

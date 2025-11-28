import os
import requests
import uuid
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
from groq import Groq
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI()

# ---------------- CONFIG ----------------
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
GROQ_KEY = os.getenv("GROQ_API_KEY")
DEEPGRAM_KEY = os.getenv("DEEPGRAM_API_KEY")
CARTESIA_KEY = os.getenv("CARTESIA_API_KEY")
BASE_URL = os.getenv("BASE_URL")

if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_NUMBER, GROQ_KEY, DEEPGRAM_KEY, CARTESIA_KEY, BASE_URL]):
    print("WARNING: One or more environment variables are missing!")

# Initialize Clients
client = Client(TWILIO_SID, TWILIO_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)

# Static directory for MP3 playback
STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ---------------- CALL USER ----------------
@app.get("/call")
def call_user(phone: str):
    if not BASE_URL:
        return {"error": "BASE_URL missing"}

    try:
        call = client.calls.create(
            to=phone,
            from_=TWILIO_NUMBER,
            url=f"{BASE_URL}/voice"
        )
        return {"status": "calling", "sid": call.sid}
    except Exception as e:
        return {"error": str(e)}

# ---------------- BEGIN CALL ----------------
@app.post("/voice", response_class=PlainTextResponse)
async def voice(request: Request):
    """Initial greeting when call starts"""
    resp = VoiceResponse()
    resp.say("Hello. I am your AI assistant. You can start speaking now.")

    # NO BEEP, NO PLAY_BEEP
    resp.record(
        action=f"{BASE_URL}/process",
        timeout=2,
        play_beep=False  # removed beep
    )
    return str(resp)

# ---------------- PROCESS USER SPEECH ----------------
@app.post("/process", response_class=PlainTextResponse)
async def process(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    audio_url = form.get("RecordingUrl")
    call_sid = form.get("CallSid", "unknown")

    if not audio_url:
        resp = VoiceResponse()
        resp.say("I didn't hear anything. Goodbye.")
        return str(resp)

    # 1️⃣ Download user audio
    try:
        wav_resp = requests.get(f"{audio_url}.wav", auth=(TWILIO_SID, TWILIO_TOKEN))
        wav_data = wav_resp.content
    except Exception:
        resp = VoiceResponse()
        resp.say("Audio download error.")
        return str(resp)

    # 2️⃣ Fast STT — Deepgram nova-2-general
    try:
        dg_resp = requests.post(
            "https://api.deepgram.com/v1/listen?model=nova-2-general&smart_format=true",
            headers={
                "Authorization": f"Token {DEEPGRAM_KEY}",
                "Content-Type": "audio/wav"
            },
            data=wav_data
        ).json()
        transcript = dg_resp.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
        print("User:", transcript)
    except Exception:
        transcript = ""

    if not transcript:
        resp = VoiceResponse()
        resp.say("Sorry, I didn't get that. Try again.")
        resp.redirect(f"{BASE_URL}/voice")
        return str(resp)

    # 3️⃣ Fast LLM — LLaMA 3.1 8B Instant
    try:
        chat_completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are a helpful phone AI. Reply in under 2 sentences. Speak naturally."},
                {"role": "user", "content": transcript}
            ]
        )
        llm_resp = chat_completion.choices[0].message.content
        print("AI:", llm_resp)
    except Exception:
        llm_resp = "I'm having a small issue thinking right now."

    # 4️⃣ Ultra-Natural TTS — Cartesia Fluency-v2
    voice_id = "d98128f8-c120-474b-ab3f-b85c1fa6da64"  # Best natural female voice

    try:
        tts_resp = requests.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={
                "X-API-Key": CARTESIA_KEY,
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json"
            },
            json={
                "model_id": "fluency-v2",
                "transcript": llm_resp,
                "voice": {
                    "mode": "id",
                    "id": voice_id,
                    "style": {
                        "emotion": "friendly",
                        "speed": 1.08,
                        "pitch": 1.1,
                        "energy": "high"
                    }
                },
                "output_format": {
                    "container": "mp3",
                    "encoding": "mp3",
                    "sample_rate": 44100,
                    "loudness": "boost"
                }
            }
        )

        if tts_resp.status_code != 200:
            raise Exception(tts_resp.text)

        filename = f"response_{call_sid}_{uuid.uuid4().hex[:6]}.mp3"
        file_path = os.path.join(STATIC_DIR, filename)

        with open(file_path, "wb") as f:
            f.write(tts_resp.content)

        # 5️⃣ Play the AI response
        resp = VoiceResponse()
        resp.play(f"{BASE_URL}/static/{filename}")

        # Loop again (still no beep)
        resp.record(
            action=f"{BASE_URL}/process",
            timeout=2,
            play_beep=False
        )

        return str(resp)

    except Exception as e:
        print("TTS Error:", e)
        resp = VoiceResponse()
        resp.say("Sorry, I had an issue speaking.")
        return str(resp)

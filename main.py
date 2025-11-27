import os
import requests
import subprocess
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, FileResponse
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# ---------------- CONFIG ----------------
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
GROQ_KEY = os.getenv("GROQ_API_KEY")
DEEPGRAM_KEY = os.getenv("DEEPGRAM_API_KEY")
CARTESIA_KEY = os.getenv("CARTESIA_API_KEY")

client = Client(TWILIO_SID, TWILIO_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)

STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)

# ---------------- CALL USER ----------------
@app.get("/call")
def call_user(phone: str):
    call = client.calls.create(
        to=phone,
        from_=TWILIO_NUMBER,
        url="https://YOUR-RENDER-URL.com/voice"
    )
    return {"status": "calling", "sid": call.sid}

# ---------------- BEGIN CALL ----------------
@app.post("/voice", response_class=PlainTextResponse)
async def voice():
    resp = VoiceResponse()
    resp.say("Hello! I am your AI assistant. Talk to me after the beep.")
    resp.record(
        action="/process",
        timeout=10,
        max_length=15,
        play_beep=True,
        transcribe=False
    )
    return str(resp)

# ---------------- PROCESS USER SPEECH ----------------
@app.post("/process", response_class=PlainTextResponse)
async def process(request: Request):
    form = await request.form()
    audio_url = form.get("RecordingUrl")

    # 1️⃣ Download user audio
    wav_file = os.path.join(STATIC_DIR, "user.wav")
    r = requests.get(f"{audio_url}.wav")
    with open(wav_file, "wb") as f:
        f.write(r.content)

    # 2️⃣ STT via Deepgram
    dg_resp = requests.post(
        "https://api.deepgram.com/v1/listen",
        headers={
            "Authorization": f"Token {DEEPGRAM_KEY}"
        },
        files={"file": open(wav_file, "rb")},
        data={"punctuate": "true"}
    ).json()
    
    transcript = dg_resp.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
    print("User said:", transcript)

    # 3️⃣ LLM Response
    llm_resp = groq_client.chat.completions.create(
        model="mixtral-8x7b-32768",
        messages=[
            {"role": "system", "content": "You are a helpful AI assistant."},
            {"role": "user", "content": transcript}
        ]
    ).choices[0].message["content"]

    print("AI reply:", llm_resp)

    # 4️⃣ TTS via Cartesia
    tts_file = os.path.join(STATIC_DIR, "response.mp3")
    tts_resp = requests.post(
        "https://play.cartesia.ai/text-to-speech",
        headers={"x-api-key": CARTESIA_KEY},
        json={"text": llm_resp, "voice": "en-US-Wavenet-F"}
    )
    with open(tts_file, "wb") as f:
        f.write(tts_resp.content)

    # 5️⃣ Twilio plays the TTS
    resp = VoiceResponse()
    resp.play(f"https://YOUR-RENDER-URL.com/static/response.mp3")
    resp.redirect("/voice")
    return str(resp)

# ---------------- Serve static MP3 ----------------
@app.get("/static/response.mp3")
def get_audio():
    return FileResponse(os.path.join(STATIC_DIR, "response.mp3"))

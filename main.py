import os
import requests
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, FileResponse
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
from groq import Groq
from dotenv import load_dotenv
from pydub import AudioSegment

load_dotenv()

app = FastAPI()

# -------- ENV CONFIG --------
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")

GROQ_KEY = os.getenv("GROQ_API_KEY")
DG_KEY = os.getenv("DEEPGRAM_API_KEY")
CARTESIA_KEY = os.getenv("CARTESIA_API_KEY")

client = Client(TWILIO_SID, TWILIO_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)

# ---------------- CALL USER ----------------
@app.get("/call")
def call_user(phone: str):
    call = client.calls.create(
        to=phone,
        from_=TWILIO_NUMBER,
        url="https://YOUR-URL.com/voice"
    )
    return {"status": "calling", "sid": call.sid}

# ---------------- CALL ANSWERED ----------------
@app.post("/voice", response_class=PlainTextResponse)
async def voice():
    r = VoiceResponse()
    r.say("Hello! I am your AI assistant. How can I help you today?")
    r.record(
        max_length=12,
        action="/process",
        play_beep=True,
        timeout=2,
        transcribe=False
    )
    return str(r)

# ---------------- PROCESS AUDIO ----------------
@app.post("/process", response_class=PlainTextResponse)
async def process(request: Request):

    form = await request.form()
    audio_url = form.get("RecordingUrl")

    # Download Twilio audio
    wav_data = requests.get(f"{audio_url}.wav").content
    with open("user.wav", "wb") as f:
        f.write(wav_data)

    # ---------- STT USING DEEPGRAM ----------
    dg_resp = requests.post(
        "https://api.deepgram.com/v1/listen",
        headers={
            "Authorization": f"Token {DG_KEY}",
            "Content-Type": "audio/wav"
        },
        data=wav_data
    ).json()

    transcript = dg_resp["results"]["channels"][0]["alternatives"][0]["transcript"]

    # ---------- LLM USING GROQ ----------
    llm_output = groq_client.chat.completions.create(
        model="mixtral-8x7b-32768",
        messages=[
            {"role": "system", "content": "You are a helpful voice AI assistant."},
            {"role": "user", "content": transcript}
        ]
    ).choices[0].message["content"]

    # ---------- TTS USING CARTESIA ----------
    tts_resp = requests.post(
        "https://api.cartesia.ai/tts",
        headers={
            "Authorization": f"Bearer {CARTESIA_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "text": llm_output,
            "voice": "sonic-english",
            "output_format": "mp3"
        }
    )

    with open("static/response.mp3", "wb") as f:
        f.write(tts_resp.content)

    # ---------- SEND BACK TO CALLER ----------
    r = VoiceResponse()
    r.play("https://YOUR-URL.com/static/response.mp3")
    r.redirect("/voice")
    return str(r)

# Serve MP3
@app.get("/static/response.mp3")
def serve_audio():
    return FileResponse("static/response.mp3")

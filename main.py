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
# 1. Get Environment Variables
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
GROQ_KEY = os.getenv("GROQ_API_KEY")
DEEPGRAM_KEY = os.getenv("DEEPGRAM_API_KEY")
CARTESIA_KEY = os.getenv("CARTESIA_API_KEY")
BASE_URL = os.getenv("BASE_URL")  # Crucial for Render

# 2. Check for missing keys (Helps debug 500 errors)
if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_NUMBER, GROQ_KEY, DEEPGRAM_KEY, CARTESIA_KEY, BASE_URL]):
    print("CRITICAL WARNING: One or more Environment Variables are missing!")

# 3. Initialize Clients
try:
    client = Client(TWILIO_SID, TWILIO_TOKEN)
    groq_client = Groq(api_key=GROQ_KEY)
except Exception as e:
    print(f"Error initializing clients: {e}")

# 4. Mount static directory
STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ---------------- CALL USER ----------------
@app.get("/call")
def call_user(phone: str):
    """Trigger an outbound call to the user."""
    if not BASE_URL:
        return {"error": "BASE_URL is not set in Environment Variables"}

    try:
        print(f"Dialing {phone} from {TWILIO_NUMBER}...")
        call = client.calls.create(
            to=phone,
            from_=TWILIO_NUMBER,
            url=f"{BASE_URL}/voice"  # Uses the variable, not a fixed string
        )
        return {"status": "calling", "sid": call.sid}
    except Exception as e:
        print(f"Twilio Error: {e}")
        return {"error": str(e)}

# ---------------- BEGIN CALL ----------------
@app.post("/voice", response_class=PlainTextResponse)
async def voice(request: Request):
    """Initial greeting when user picks up."""
    resp = VoiceResponse()
    resp.say("Hello. I am your AI assistant. Please speak after the beep.")
    resp.record(
        action=f"{BASE_URL}/process", 
        timeout=2,              
        play_beep=True
    )
    return str(resp)

# ---------------- PROCESS USER SPEECH ----------------
@app.post("/process", response_class=PlainTextResponse)
async def process(request: Request, background_tasks: BackgroundTasks):
    """Handle the recording, transcribe, think, and speak."""
    form = await request.form()
    audio_url = form.get("RecordingUrl")
    call_sid = form.get("CallSid", "unknown")

    print(f"Processing recording: {audio_url}")

    if not audio_url:
        resp = VoiceResponse()
        resp.say("I didn't hear anything. Goodbye.")
        return str(resp)

    # 1️⃣ Download user audio (With Twilio Auth)
    try:
        wav_resp = requests.get(f"{audio_url}.wav", auth=(TWILIO_SID, TWILIO_TOKEN))
        wav_data = wav_resp.content
    except Exception as e:
        print(f"Download Error: {e}")
        resp = VoiceResponse()
        resp.say("Connection error. Goodbye.")
        return str(resp)

    # 2️⃣ STT via Deepgram
    try:
        dg_resp = requests.post(
            "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true",
            headers={
                "Authorization": f"Token {DEEPGRAM_KEY}",
                "Content-Type": "audio/wav"
            },
            data=wav_data
        ).json()
        transcript = dg_resp.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
        print(f"User said: {transcript}")
    except Exception as e:
        print(f"Deepgram Error: {e}")
        transcript = ""

    if not transcript:
        resp = VoiceResponse()
        resp.say("I didn't catch that. Please say it again.")
        resp.redirect(f"{BASE_URL}/voice")
        return str(resp)

    # 3️⃣ LLM Response (Groq)
    try:
        chat_completion = groq_client.chat.completions.create(
            model="mixtral-8x7b-32768",
            messages=[
                {"role": "system", "content": "You are a helpful phone assistant. Be extremely concise. Keep answers under 2 sentences."},
                {"role": "user", "content": transcript}
            ]
        )
        llm_resp = chat_completion.choices[0].message.content
        print(f"AI reply: {llm_resp}")
    except Exception as e:
        print(f"Groq Error: {e}")
        llm_resp = "I am having trouble thinking right now."

    # 4️⃣ TTS via Cartesia
    voice_id = "a0e99841-438c-4a64-b679-ae501e7d6091" # Generic Male Voice
    
    try:
        tts_resp = requests.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={
                "X-API-Key": CARTESIA_KEY,
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json"
            },
            json={
                "model_id": "sonic-english",
                "transcript": llm_resp,
                "voice": {"mode": "id", "id": voice_id},
                "output_format": {"container": "mp3", "encoding": "mp3", "sample_rate": 44100}
            }
        )

        if tts_resp.status_code != 200:
            print(f"Cartesia Error: {tts_resp.text}")
            raise Exception("TTS Failed")

        # Save file with unique name
        filename = f"response_{call_sid}_{uuid.uuid4().hex[:6]}.mp3"
        file_path = os.path.join(STATIC_DIR, filename)
        
        with open(file_path, "wb") as f:
            f.write(tts_resp.content)

        # 5️⃣ Play Audio
        resp = VoiceResponse()
        resp.play(f"{BASE_URL}/static/{filename}")
        
        # Loop back to record again
        resp.record(
            action=f"{BASE_URL}/process",
            timeout=2,
            play_beep=True
        )
        
        return str(resp)

    except Exception as e:
        print(f"Processing Error: {e}")
        resp = VoiceResponse()
        resp.say("Sorry, I encountered an error.")
        return str(resp)
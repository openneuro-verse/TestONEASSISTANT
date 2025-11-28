import os
import requests
import uuid
import time
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
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
BASE_URL = os.getenv("BASE_URL")

# Initialize Clients
client = Client(TWILIO_SID, TWILIO_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)

# Mount static directory
STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ---------------- CALL USER ----------------
@app.get("/call")
def call_user(phone: str):
    if not BASE_URL:
        return {"error": "BASE_URL missing"}
    
    call = client.calls.create(
        to=phone,
        from_=TWILIO_NUMBER,
        url=f"{BASE_URL}/voice"
    )
    return {"status": "calling", "sid": call.sid}

# ---------------- BEGIN CALL ----------------
@app.post("/voice", response_class=PlainTextResponse)
async def voice(request: Request):
    resp = VoiceResponse()
    
    # "gather" is faster than "record" for detecting silence
    # But for open-ended conversation, we stick to record with tighter settings
    resp.say("Hello. I'm listening.") 
    
    resp.record(
        action=f"{BASE_URL}/process",
        timeout=1,           # Wait only 1 second of silence to detect end of speech
        max_length=10,       # Limit recording to 10s to force speed
        play_beep=False,     # <--- REMOVED THE BEEP
        trim="trim-silence"  # Remove empty audio at start/end
    )
    return str(resp)

# ---------------- PROCESS USER SPEECH ----------------
@app.post("/process", response_class=PlainTextResponse)
async def process(request: Request, background_tasks: BackgroundTasks):
    start_time = time.time()
    form = await request.form()
    audio_url = form.get("RecordingUrl")
    
    if not audio_url:
        resp = VoiceResponse()
        resp.say("Are you still there?")
        resp.redirect(f"{BASE_URL}/voice")
        return str(resp)

    # 1. Download Audio (Fastest method)
    wav_resp = requests.get(f"{audio_url}.wav", auth=(TWILIO_SID, TWILIO_TOKEN))
    wav_data = wav_resp.content

    # 2. STT (Deepgram Nova-2 Phone Model) -> Optimized for telephone audio
    dg_resp = requests.post(
        "https://api.deepgram.com/v1/listen?model=nova-2-phone&smart_format=true",
        headers={"Authorization": f"Token {DEEPGRAM_KEY}", "Content-Type": "audio/wav"},
        data=wav_data
    ).json()
    
    transcript = dg_resp.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
    print(f"User: {transcript}")

    if not transcript:
        resp = VoiceResponse()
        resp.redirect(f"{BASE_URL}/voice") # Loop immediately if silence
        return str(resp)

    # 3. LLM (Groq - Llama 3 for speed)
    # Using Llama 3 8b is faster than Mixtral
    chat_completion = groq_client.chat.completions.create(
        model="llama3-8b-8192", 
        messages=[
            {
                "role": "system", 
                # PROMPT ENGINEERING FOR REALISM
                "content": "You are a helpful friend, not a robot. Speak casually. Do not use lists. Keep answers very short (under 10 words)."
            },
            {"role": "user", "content": transcript}
        ],
        max_tokens=50 # Force short answers for speed
    )
    llm_resp = chat_completion.choices[0].message.content
    print(f"AI: {llm_resp}")

    # 4. TTS (Cartesia - Optimized for Phone)
    # Voice: 'Salesman' style (more punchy/loud)
    voice_id = "5c3c89e5-535f-442d-9916-466d34cc62a4" 
    
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
            "output_format": {
                "container": "wav", 
                "encoding": "pcm_s16le", 
                "sample_rate": 8000 # <--- KEY FIX FOR LOUDNESS (Matches Phone Network)
            }
        }
    )

    filename = f"resp_{uuid.uuid4().hex[:4]}.wav"
    file_path = os.path.join(STATIC_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(tts_resp.content)

    print(f"Total processing time: {time.time() - start_time:.2f}s")

    # 5. Play Response
    resp = VoiceResponse()
    resp.play(f"{BASE_URL}/static/{filename}")
    
    # Loop back instantly without beep
    resp.record(
        action=f"{BASE_URL}/process",
        timeout=1,
        play_beep=False
    )
    
    return str(resp)
from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import whisper
import openai
import os
import tempfile
import base64
import warnings
import json
from dotenv import load_dotenv

# Suppress Whisper FP16 warnings
warnings.filterwarnings("ignore", category=UserWarning, module="whisper")

load_dotenv()

app = FastAPI(title="RapidAnswer API", version="1.0.0")

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize OpenAI client
openai.api_key = os.getenv("OPENAI_API_KEY")

# Load Whisper model (using base model for faster processing)
whisper_model = whisper.load_model("base")


def transcribe_audio_internal(audio_data: bytes) -> str:
    """
    Transcribe audio data to text using OpenAI Whisper
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
        temp_audio.write(audio_data)
        temp_audio_path = temp_audio.name

    try:
        result = whisper_model.transcribe(temp_audio_path)
        return result["text"]
    finally:
        os.unlink(temp_audio_path)


def get_ai_response_internal(text: str) -> str:
    """
    Get AI response from OpenAI API
    """
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Keep responses conversational and concise."},
            {"role": "user", "content": text}
        ],
        max_tokens=150
    )
    return response.choices[0].message.content


def synthesize_speech_internal(text: str) -> str:
    """
    Convert text to speech using OpenAI TTS and return base64 encoded audio
    """
    response = openai.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=text
    )
    return base64.b64encode(response.content).decode('utf-8')


@app.get("/")
async def root():
    return {"message": "RapidAnswer API is running!"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket connection established")

    try:
        while True:
            # Receive message from client
            message = await websocket.receive_text()
            data = json.loads(message)

            if data["type"] == "process_voice":
                print("Processing voice message via WebSocket")

                # Decode base64 audio data
                audio_data = base64.b64decode(data["audio_data"])
                print(f"Received {len(audio_data)} bytes of audio data")

                # Step 1: Transcribe audio to text
                print("Starting transcription...")
                transcription = transcribe_audio_internal(audio_data)
                print(f"Transcription result: {transcription}")

                # Step 2: Get AI response
                print("Getting AI response...")
                ai_response = get_ai_response_internal(transcription)
                print(f"AI response: {ai_response}")

                # Step 3: Convert response to speech
                print("Converting to speech...")
                audio_base64 = synthesize_speech_internal(ai_response)
                print("Speech synthesis complete")

                # Send response back to client
                response = {
                    "type": "voice_response",
                    "transcription": transcription,
                    "ai_response": ai_response,
                    "audio": audio_base64
                }

                await websocket.send_text(json.dumps(response))

    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        await websocket.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
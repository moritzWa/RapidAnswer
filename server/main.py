from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import openai
import os
import base64
import json
import asyncio
import websockets
import ssl
from dotenv import load_dotenv

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

# Deepgram configuration
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"


async def transcribe_audio_deepgram_streaming(audio_data: bytes, websocket: WebSocket) -> str:
    """
    Transcribe audio data to text using Deepgram with real-time streaming
    """
    # Use linear16 PCM format for raw audio data
    uri = f"{DEEPGRAM_URL}?model=nova-2&smart_format=true&encoding=linear16&sample_rate=16000&channels=1&interim_results=true"

    final_transcript = ""

    try:
        # Create SSL context for secure connection
        ssl_context = ssl.create_default_context()

        # Create WebSocket connection with SSL and authorization
        async with websockets.connect(
            uri,
            additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
            ssl=ssl_context
        ) as ws:
            # Send audio data
            await ws.send(audio_data)

            # Send close stream signal
            await ws.send(json.dumps({"type": "CloseStream"}))

            # Receive transcription
            async for message in ws:
                data = json.loads(message)

                # Handle interim results for real-time feedback
                if data.get("type") == "Results":
                    transcript = data.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")

                    if transcript:
                        if data.get("is_final"):
                            final_transcript += transcript + " "
                        else:
                            # Send interim result to client
                            interim_response = {
                                "type": "interim_transcription",
                                "text": transcript
                            }
                            await websocket.send_text(json.dumps(interim_response))

    except Exception as e:
        print(f"Deepgram error: {e}")
        # Raise exception instead of returning error message
        raise Exception(f"Transcription failed: {e}")

    if not final_transcript.strip():
        raise Exception("No transcription received")

    return final_transcript.strip()


async def get_ai_response_streaming(text: str, websocket: WebSocket) -> str:
    """
    Get AI response from OpenAI API with streaming
    """
    full_response = ""

    try:
        stream = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Keep responses conversational and concise."},
                {"role": "user", "content": text}
            ],
            stream=True
        )

        for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                content = chunk.choices[0].delta.content
                full_response += content

                # Send streaming response to client
                stream_response = {
                    "type": "ai_response_stream",
                    "content": content,
                    "is_complete": False
                }
                await websocket.send_text(json.dumps(stream_response))

        # Send completion signal
        completion_response = {
            "type": "ai_response_stream",
            "content": "",
            "is_complete": True
        }
        await websocket.send_text(json.dumps(completion_response))

    except Exception as e:
        print(f"OpenAI streaming error: {e}")
        raise Exception(f"AI response generation failed: {e}")

    return full_response


async def synthesize_speech_streaming(text: str, websocket: WebSocket) -> str:
    """
    Convert text to speech using OpenAI TTS with 2x speed and stream audio back
    """
    try:
        response = openai.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text,
            speed=2.0  # 2x speed as requested
        )

        # Get audio content
        audio_content = response.content
        audio_base64 = base64.b64encode(audio_content).decode('utf-8')

        # Send audio chunks in smaller pieces for streaming
        chunk_size = 8192  # 8KB chunks
        for i in range(0, len(audio_base64), chunk_size):
            chunk = audio_base64[i:i + chunk_size]
            is_final = i + chunk_size >= len(audio_base64)

            stream_response = {
                "type": "audio_stream",
                "audio_chunk": chunk,
                "is_final": is_final
            }
            await websocket.send_text(json.dumps(stream_response))

        return audio_base64

    except Exception as e:
        print(f"TTS streaming error: {e}")
        raise Exception(f"Speech synthesis failed: {e}")


@app.get("/")
async def root():
    return {"message": "RapidAnswer API is running!"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket connection established")

    audio_buffer = bytearray()

    try:
        while True:
            # Handle both binary (audio) and text (control) messages
            try:
                message = await websocket.receive()
            except Exception as e:
                print(f"WebSocket receive error: {e}")
                break

            # Check if client disconnected
            if message["type"] == "websocket.disconnect":
                print("Client disconnected")
                break

            if message["type"] == "websocket.receive":
                if "bytes" in message:
                    # Binary audio chunk
                    audio_chunk = message["bytes"]
                    audio_buffer.extend(audio_chunk)
                    print(f"Received audio chunk: {len(audio_chunk)} bytes, total: {len(audio_buffer)}")

                elif "text" in message:
                    # JSON control message
                    data = json.loads(message["text"])

                    if data["type"] == "user_audio_end":
                        print("Audio stream ended, processing...")

                        if len(audio_buffer) > 0:
                            # Process complete audio buffer
                            print(f"Processing {len(audio_buffer)} bytes of audio data")

                            try:
                                # Step 1: Transcribe audio to text
                                print("Starting transcription...")
                                transcription = await transcribe_audio_deepgram_streaming(bytes(audio_buffer), websocket)
                                print(f"Transcription result: {transcription}")

                                # Step 2: Get AI response with streaming
                                print("Getting AI response...")
                                ai_response = await get_ai_response_streaming(transcription, websocket)
                                print(f"AI response: {ai_response}")

                                # Step 3: Convert response to speech with streaming
                                print("Converting to speech...")
                                audio_base64 = await synthesize_speech_streaming(ai_response, websocket)
                                print("Speech synthesis complete")

                                # Send final response back to client
                                response = {
                                    "type": "voice_response",
                                    "transcription": transcription,
                                    "ai_response": ai_response,
                                    "audio": audio_base64
                                }

                                await websocket.send_text(json.dumps(response))

                            except Exception as processing_error:
                                print(f"Processing error: {processing_error}")
                                # Send error to client instead of continuing
                                error_response = {
                                    "type": "error",
                                    "message": str(processing_error)
                                }
                                await websocket.send_text(json.dumps(error_response))

                            # Clear buffer for next recording
                            audio_buffer.clear()

    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        try:
            if websocket.client_state.value == 1:  # OPEN state
                await websocket.close()
        except:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
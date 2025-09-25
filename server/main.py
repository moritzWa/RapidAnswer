from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import asyncio
from dotenv import load_dotenv
from deepgram import DeepgramClient, DeepgramClientOptions, LiveOptions
from deepgram_handler import get_transcript_generator
from ai_handlers import handle_ai_response

load_dotenv()

app = FastAPI(title="RapidAnswer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

if DEEPGRAM_API_KEY is None:
    print("❌ DEEPGRAM_API_KEY not found in environment variables.")
    exit(1)
else:
    print("✅ DEEPGRAM_API_KEY loaded.")

@app.get("/")
async def root():
    return {"message": "RapidAnswer API is running!"}


@app.websocket("/ws")
async def websocket_endpoint(client_websocket: WebSocket):
    await client_websocket.accept()
    print("WebSocket connection established")

    ai_task = None
    chat_history = []  # Store conversation history for this connection
    is_tts_playing = False  # Track TTS playback state for interruption
    
    try:
        config = DeepgramClientOptions(options={"keepalive": "true"})
        deepgram: DeepgramClient = DeepgramClient(DEEPGRAM_API_KEY, config)
        dg_connection = deepgram.listen.asynclive.v("1")
        
        # Pass TTS state and AI task getters to transcript generator for interruption detection
        transcript_generator = get_transcript_generator(
            client_websocket, 
            dg_connection,
            lambda: is_tts_playing,
            lambda: ai_task
        )

        options = LiveOptions(
            model="nova-2",
            punctuate=True,
            language="en-US",
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            smart_format=True,
            interim_results=True,  # Required for utterance_end_ms
            endpointing=1200,  # Balance: 1.2s to allow complete thoughts
            utterance_end_ms=2500,  # Longer backup for complete utterances
            no_delay=True,  # Fix for speech_final not triggering
        )
        await dg_connection.start(options)

        async def forward_audio():
            try:
                while True:
                    message = await client_websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        print("Client disconnected. Closing Deepgram connection.")
                        await dg_connection.finish()
                        break
                    if message["type"] == "websocket.receive" and "bytes" in message:
                        await dg_connection.send(message["bytes"])
                    elif message["type"] == "websocket.receive" and "text" in message:
                        data = json.loads(message["text"])
                        if data.get("type") == "user_audio_end":
                            print("Client sent stop signal. Closing stream.")
                            await dg_connection.finish()
                            break
            except Exception as e:
                print(f"Error forwarding audio: {e}")
            finally:
                if dg_connection:
                    await dg_connection.finish()

        async def handle_transcripts():
            nonlocal ai_task, chat_history, is_tts_playing

            async for complete_transcript in transcript_generator:
                # transcript_generator now only yields when speech_final=True
                print(f"User finished speaking. Complete transcript: '{complete_transcript}'")

                # Cancel any ongoing AI response (barge-in)
                if ai_task and not ai_task.done():
                    print("Barge-in detected. Cancelling previous AI response.")
                    ai_task.cancel()
                    await client_websocket.send_text(json.dumps({"type": "stop_audio_playback"}))
                    is_tts_playing = False  # Stop playing on cancellation
                
                # Only reset TTS state if it wasn't already interrupted
                # This keeps the state accurate for interruption detection
                if is_tts_playing:
                    is_tts_playing = False

                # Start AI response immediately (no additional timer needed)
                is_tts_playing = True  # Mark TTS as starting
                ai_task = asyncio.create_task(handle_ai_response(complete_transcript, client_websocket, chat_history))

                try:
                    # Wait for AI task to complete and update chat history
                    ai_response = await ai_task
                    # Don't set is_tts_playing to False here - audio is still playing on client!
                    # It will be set to False when the next utterance starts
                except asyncio.CancelledError:
                    print("AI task was cancelled due to interruption")
                    ai_response = None
                    is_tts_playing = False  # Only set to False on cancellation
                if ai_response:
                    # Update chat history with this successful exchange
                    chat_history.append({"role": "user", "content": complete_transcript})
                    chat_history.append({"role": "assistant", "content": ai_response})

                    # No message limit - keep full conversation history

        await asyncio.gather(forward_audio(), handle_transcripts())

    except Exception as e:
        print(f"❌ WebSocket handler error: {e}")
    finally:
        if ai_task and not ai_task.done():
            ai_task.cancel()
        try:
            await client_websocket.close()
            print("🔌 WebSocket connection closed")
        except Exception:
            print("🔌 WebSocket connection already closed.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
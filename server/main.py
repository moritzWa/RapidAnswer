from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import openai
from openai import AsyncOpenAI
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

# Initialize OpenAI clients
openai.api_key = os.getenv("OPENAI_API_KEY")
async_openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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


async def get_ai_response_with_sentence_streaming(text: str, websocket: WebSocket) -> str:
    """
    Get AI response from OpenAI API with sentence-by-sentence TTS streaming
    """
    full_response = ""
    sentence_buffer = ""
    tts_tasks = []
    audio_queue = asyncio.Queue()

    # The first sentence doesn't have to wait for anything.
    previous_sentence_done = asyncio.Event()
    previous_sentence_done.set()

    async def audio_sender():
        """Get audio chunks from queue and send to client."""
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                # Sentinel value received, stop sending
                audio_queue.task_done()
                break
            await websocket.send_text(json.dumps(chunk))
            audio_queue.task_done()

    # Start the dedicated audio sender task
    sender_task = asyncio.create_task(audio_sender())

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
                sentence_buffer += content

                # Send streaming response to client
                stream_response = {
                    "type": "ai_response_stream",
                    "content": content,
                    "is_complete": False
                }
                await websocket.send_text(json.dumps(stream_response))

                # Check if we have a complete sentence
                if any(punct in content for punct in ['.', '!', '?']):
                    # Found sentence-ending punctuation
                    complete_sentence = sentence_buffer.strip()
                    if len(complete_sentence) > 5:  # Only process meaningful sentences
                        print(f"🎵 Starting TTS for sentence: {complete_sentence}")

                        # This event will be set when the current sentence is done.
                        current_sentence_done = asyncio.Event()

                        # Start TTS, passing the gates for ordering.
                        task = asyncio.create_task(synthesize_speech_streaming(
                            text=complete_sentence,
                            audio_queue=audio_queue,
                            wait_for_event=previous_sentence_done,
                            set_event_when_done=current_sentence_done
                        ))
                        tts_tasks.append(task)

                        # The next sentence will wait for this one to be done.
                        previous_sentence_done = current_sentence_done

                    sentence_buffer = ""  # Reset buffer

        # Send completion signal for the text stream
        completion_response = {
            "type": "ai_response_stream",
            "content": "",
            "is_complete": True
        }
        await websocket.send_text(json.dumps(completion_response))

        # Handle any remaining text in buffer
        if sentence_buffer.strip():
            print(f"🎵 Starting TTS for final fragment: {sentence_buffer.strip()}")
            current_sentence_done = asyncio.Event()
            task = asyncio.create_task(synthesize_speech_streaming(
                text=sentence_buffer.strip(),
                audio_queue=audio_queue,
                wait_for_event=previous_sentence_done,
                set_event_when_done=current_sentence_done
            ))
            tts_tasks.append(task)

        # Wait for all TTS tasks to complete before returning
        if tts_tasks:
            await asyncio.gather(*tts_tasks)

    except Exception as e:
        print(f"OpenAI streaming error: {e}")
        raise Exception(f"AI response generation failed: {e}")
    finally:
        # Signal the sender to stop and wait for it to finish
        await audio_queue.put(None)
        await sender_task

    return full_response


async def synthesize_speech_streaming(
    text: str,
    audio_queue: asyncio.Queue,
    wait_for_event: asyncio.Event,
    set_event_when_done: asyncio.Event
) -> None:
    """
    Convert text to speech using OpenAI's streaming TTS API and put chunks in a queue,
    respecting an event chain for ordering.
    """
    try:
        # Use AsyncOpenAI client for streaming response
        async with async_openai_client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice="alloy",
            input=text,
            speed=2.0,  # 2x speed as requested
            response_format="pcm"  # Raw PCM for lowest latency
        ) as response:
            # Wait for the previous sentence's audio to be fully queued.
            await wait_for_event.wait()

            # Stream audio chunks as they're generated by OpenAI
            async for chunk in response.iter_bytes(chunk_size=4096):
                if chunk:
                    # Send PCM chunk immediately for real-time playback
                    stream_response = {
                        "type": "audio_stream_pcm",
                        "pcm_chunk": base64.b64encode(chunk).decode('utf-8'),
                        "sample_rate": 24000,  # OpenAI TTS default sample rate
                        "channels": 1,
                        "is_final": False
                    }
                    await audio_queue.put(stream_response)

            # Add a 200ms silent audio chunk for a natural pause
            sample_rate = 24000
            duration = 0.2  # 200ms
            sample_width = 2  # 16-bit PCM
            num_samples = int(sample_rate * duration)
            num_bytes = num_samples * sample_width
            silent_chunk = b'\x00' * num_bytes

            silent_response = {
                "type": "audio_stream_pcm",
                "pcm_chunk": base64.b64encode(silent_chunk).decode('utf-8'),
                "sample_rate": sample_rate,
                "channels": 1,
                "is_final": False
            }
            await audio_queue.put(silent_response)

            # Send final signal for the sentence
            final_response = {
                "type": "audio_stream_pcm",
                "pcm_chunk": "",
                "sample_rate": 24000,
                "channels": 1,
                "is_final": True
            }
            await audio_queue.put(final_response)

    except Exception as e:
        print(f"TTS streaming error: {e}")
        # We don't re-raise here to avoid crashing the entire process
        # The client will simply not receive audio for this sentence.
    finally:
        # Signal that this sentence is done, allowing the next one to proceed.
        set_event_when_done.set()


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

                                # Step 2: Get AI response with sentence-by-sentence TTS streaming
                                print("Getting AI response with sentence streaming...")
                                ai_response = await get_ai_response_with_sentence_streaming(transcription, websocket)
                                print(f"AI response complete: {ai_response}")

                                # Note: TTS is now handled within the AI response function
                                print("All TTS streaming complete")

                                # Send final response back to client (audio already streamed)
                                response = {
                                    "type": "voice_response",
                                    "transcription": transcription,
                                    "ai_response": ai_response,
                                    "audio": ""  # Audio was already streamed sentence-by-sentence
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
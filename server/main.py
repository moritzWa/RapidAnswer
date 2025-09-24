from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import openai
from openai import AsyncOpenAI
import os
import base64
import json
import asyncio
from dotenv import load_dotenv
from deepgram_handler import (
    create_deepgram_connection,
    handle_deepgram_messages,
    send_close_stream,
    forward_audio_chunk,
    reset_audio_counter,
    total_audio_bytes_sent
)

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


async def handle_ai_response(transcription: str, client_websocket: WebSocket, cleanup_callback=None):
    """
    Process final transcription and generate AI response with TTS streaming
    """
    print(f"📝 Starting AI response for: '{transcription}'")
    try:
        ai_response = await get_ai_response_with_sentence_streaming(
            transcription,
            client_websocket
        )
        print(f"✅ AI response completed: '{ai_response[:50]}...'")

        # Send final response back to client
        response = {
            "type": "voice_response",
            "transcription": transcription,
            "ai_response": ai_response,
            "audio": ""  # Audio was already streamed
        }
        await client_websocket.send_text(json.dumps(response))
        print(f"📤 Sent final voice_response to client")

    except Exception as e:
        print(f"❌ AI response error: {e}")
        import traceback
        print(f"🔍 Full traceback: {traceback.format_exc()}")
        error_response = {
            "type": "error",
            "message": f"AI response failed: {e}"
        }
        try:
            await client_websocket.send_text(json.dumps(error_response))
        except Exception as send_error:
            print(f"❌ Failed to send error response: {send_error}")
    finally:
        # Clean up Deepgram connection after AI processing is complete
        if cleanup_callback:
            await cleanup_callback()


async def manage_audio_queue(audio_queue: asyncio.Queue, websocket: WebSocket):
    """Send audio chunks from queue to client"""
    print("🎵 Audio queue manager started")
    try:
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                # Sentinel value received, stop sending
                print("🛑 Audio queue received stop signal")
                audio_queue.task_done()
                break

            # Log final audio chunks
            if chunk.get("is_final"):
                print(f"🎯 Sending final audio chunk: {chunk.get('type')}")

            await websocket.send_text(json.dumps(chunk))
            audio_queue.task_done()
    except Exception as e:
        print(f"❌ Audio queue error: {e}")
        import traceback
        print(f"🔍 Audio queue traceback: {traceback.format_exc()}")
    finally:
        print("🏁 Audio queue manager stopped")


async def stream_openai_response(text: str, websocket: WebSocket, sentence_handler):
    """Stream AI response and detect complete sentences"""
    full_response = ""
    sentence_buffer = ""

    # Handle empty/unclear audio gracefully
    if text == "[AUDIO_UNCLEAR]":
        user_message = "I didn't hear you clearly. Could you repeat that?"
        system_message = "You are a helpful assistant. The user's audio was unclear, so respond as if you didn't hear them properly. Keep responses conversational and concise."
    else:
        user_message = text
        system_message = "You are a helpful assistant. Keep responses conversational and concise."

    stream = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message}
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
                    await sentence_handler(complete_sentence)
                sentence_buffer = ""  # Reset buffer

    # Send completion signal for the text stream
    completion_response = {
        "type": "ai_response_stream",
        "content": "",
        "is_complete": True
    }
    await websocket.send_text(json.dumps(completion_response))

    return full_response, sentence_buffer


async def get_ai_response_with_sentence_streaming(text: str, websocket: WebSocket) -> str:
    """
    Get AI response from OpenAI API with sentence-by-sentence TTS streaming
    """
    tts_tasks = []
    audio_queue = asyncio.Queue()

    # The first sentence doesn't have to wait for anything.
    previous_sentence_done = asyncio.Event()
    previous_sentence_done.set()

    # Start the dedicated audio sender task
    sender_task = asyncio.create_task(manage_audio_queue(audio_queue, websocket))

    async def handle_sentence(sentence):
        """Process a complete sentence for TTS"""
        nonlocal previous_sentence_done
        print(f"🎵 Starting TTS for sentence: {sentence}")

        # This event will be set when the current sentence is done.
        current_sentence_done = asyncio.Event()

        # Start TTS, passing the gates for ordering.
        task = asyncio.create_task(synthesize_speech_streaming(
            text=sentence,
            audio_queue=audio_queue,
            wait_for_event=previous_sentence_done,
            set_event_when_done=current_sentence_done
        ))
        tts_tasks.append(task)

        # The next sentence will wait for this one to be done.
        previous_sentence_done = current_sentence_done

    try:
        # Stream response and handle sentences
        full_response, remaining_buffer = await stream_openai_response(text, websocket, handle_sentence)

        # Handle any remaining text in buffer
        if remaining_buffer.strip():
            print(f"🎵 Starting TTS for final fragment: {remaining_buffer.strip()}")
            await handle_sentence(remaining_buffer.strip())

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
    print(f"🎤 Starting TTS for: '{text[:30]}...'")
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
            print(f"✅ TTS completed for: '{text[:30]}...'")

    except Exception as e:
        print(f"❌ TTS streaming error for '{text[:30]}...': {e}")
        import traceback
        print(f"🔍 TTS traceback: {traceback.format_exc()}")
        # We don't re-raise here to avoid crashing the entire process
        # The client will simply not receive audio for this sentence.
    finally:
        # Signal that this sentence is done, allowing the next one to proceed.
        print(f"🔄 TTS event set for: '{text[:30]}...'")
        set_event_when_done.set()


@app.get("/")
async def root():
    return {"message": "RapidAnswer API is running!"}


@app.websocket("/ws")
async def websocket_endpoint(client_websocket: WebSocket):
    await client_websocket.accept()
    print("WebSocket connection established")

    deepgram_websocket = None
    deepgram_task = None

    async def cleanup_deepgram():
        """Clean up Deepgram connection after AI processing completes"""
        nonlocal deepgram_websocket, deepgram_task
        if deepgram_websocket:
            try:
                await deepgram_websocket.close()
                print("🔄 Deepgram connection closed, ready for next turn")
            except Exception as close_error:
                print(f"⚠️  Error closing Deepgram: {close_error}")
            finally:
                deepgram_websocket = None

        if deepgram_task:
            deepgram_task.cancel()
            try:
                await deepgram_task
            except asyncio.CancelledError:
                print("🛑 Deepgram task cancelled for next turn")
            finally:
                deepgram_task = None

    try:
        while True:
            # Handle both binary (audio) and text (control) messages
            try:
                message = await client_websocket.receive()
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

                    # Create fresh Deepgram connection for new recording session
                    if deepgram_websocket is None:
                        try:
                            print("🎙️ Creating new Deepgram connection for recording session")
                            reset_audio_counter()  # Reset byte counter for new session
                            deepgram_websocket = await create_deepgram_connection(DEEPGRAM_URL, DEEPGRAM_API_KEY)
                            # Start listening task for Deepgram responses
                            deepgram_task = asyncio.create_task(
                                handle_deepgram_messages(deepgram_websocket, client_websocket,
                                                        lambda transcript, ws: handle_ai_response(transcript, ws, cleanup_deepgram))
                            )
                            print("✅ Deepgram WebSocket connection established")
                        except Exception as e:
                            print(f"❌ Failed to create Deepgram connection: {e}")
                            error_response = {
                                "type": "error",
                                "message": f"Failed to connect to transcription service: {e}"
                            }
                            await client_websocket.send_text(json.dumps(error_response))
                            continue

                    # Forward audio chunk directly to Deepgram (if still connected)
                    try:
                        await forward_audio_chunk(deepgram_websocket, audio_chunk)
                    except Exception as forward_error:
                        print(f"⚠️  Could not forward audio to Deepgram: {forward_error}")
                        # Don't crash the WebSocket handler for Deepgram issues

                elif "text" in message:
                    # JSON control message
                    data = json.loads(message["text"])

                    if data["type"] == "user_audio_end":
                        print(f"🛑 Audio stream ended, sending CloseStream to Deepgram... (Total audio sent: {total_audio_bytes_sent} bytes)")

                        # Send close stream signal to Deepgram
                        if deepgram_websocket:
                            await send_close_stream(deepgram_websocket)
                            # Don't close Deepgram yet - let AI processing complete first
                        else:
                            print("⚠️ user_audio_end received but no deepgram_websocket exists!")

    except Exception as e:
        print(f"❌ WebSocket handler error: {str(e)}")
        import traceback
        print(f"🔍 WebSocket handler traceback: {traceback.format_exc()}")
        try:
            if client_websocket.client_state.value == 1:  # OPEN state
                print("🔌 Closing client WebSocket due to error")
                await client_websocket.close()
        except Exception as close_error:
            print(f"❌ Error closing WebSocket: {close_error}")
    finally:
        # Cleanup Deepgram connection
        if deepgram_websocket:
            try:
                await deepgram_websocket.close()
                print("🔌 Deepgram WebSocket connection closed")
            except Exception as cleanup_error:
                print(f"⚠️  Error closing Deepgram connection: {cleanup_error}")

        # Cancel Deepgram listening task
        if deepgram_task:
            deepgram_task.cancel()
            try:
                await deepgram_task
            except asyncio.CancelledError:
                print("🛑 Deepgram task cancelled")
                pass
            except Exception as task_error:
                print(f"⚠️  Error cancelling Deepgram task: {task_error}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
import openai
import json
import asyncio
import base64
from openai import AsyncOpenAI
from fastapi import WebSocket
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize OpenAI clients
openai.api_key = os.getenv("OPENAI_API_KEY")
async_openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


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

            await websocket.send_text(json.dumps(chunk))
            audio_queue.task_done()
    except Exception as e:
        print(f"❌ Audio queue error: {e}")
    finally:
        print("🏁 Audio queue manager stopped")


async def get_ai_response_with_sentence_streaming(text: str, websocket: WebSocket) -> str:
    """
    Get AI response from OpenAI API with sentence-by-sentence TTS streaming
    """
    from ai_handlers import stream_openai_response # Import here to avoid circular dependency
    tts_tasks = []
    audio_queue = asyncio.Queue()
    full_response = ""

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

    except asyncio.CancelledError:
        print("AI response task cancelled.")
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
        async with async_openai_client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice="alloy",
            input=text,
            speed=2.0,  # 2x speed as requested
            response_format="pcm"  # Raw PCM for lowest latency
        ) as response:
            await wait_for_event.wait()

            async for chunk in response.iter_bytes(chunk_size=4096):
                if chunk:
                    stream_response = {
                        "type": "audio_stream_pcm",
                        "pcm_chunk": base64.b64encode(chunk).decode('utf-8'),
                        "sample_rate": 24000,
                        "channels": 1,
                    }
                    await audio_queue.put(stream_response)

    except asyncio.CancelledError:
        print(f"TTS task for '{text[:30]}...' cancelled.")
    except Exception as e:
        print(f"❌ TTS streaming error for '{text[:30]}...': {e}")
    finally:
        set_event_when_done.set()

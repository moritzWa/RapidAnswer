import json
import ssl
import websockets
import asyncio
from fastapi import WebSocket


async def create_deepgram_connection(deepgram_url: str, api_key: str):
    """
    Create a WebSocket connection to Deepgram for real-time transcription
    """
    uri = f"{deepgram_url}?model=nova-2&smart_format=true&encoding=linear16&sample_rate=16000&channels=1&interim_results=true&endpointing=false"

    try:
        ssl_context = ssl.create_default_context()
        deepgram_websocket = await websockets.connect(
            uri,
            additional_headers={"Authorization": f"Token {api_key}"},
            ssl=ssl_context
        )
        return deepgram_websocket
    except Exception as e:
        print(f"Deepgram connection error: {e}")
        raise Exception(f"Failed to connect to Deepgram: {e}")


async def handle_deepgram_messages(deepgram_websocket, client_websocket):
    """
    Handle incoming messages from Deepgram WebSocket, forward interim results,
    and return the final transcript.
    """
    full_transcript = ""
    session_completed = False

    try:
        async for message in deepgram_websocket:
            data = json.loads(message)

            if data.get("type") == "Results":
                alternatives = data.get("channel", {}).get("alternatives", [])
                if alternatives:
                    transcript = alternatives[0].get("transcript", "")
                    if transcript:
                        if data.get("is_final"):
                            full_transcript += transcript + " "
                            # Don't send final transcript to AI here; wait for client signal
                        else:
                            # Forward interim result to client immediately
                            interim_response = {
                                "type": "interim_transcription",
                                "text": transcript
                            }
                            await client_websocket.send_text(json.dumps(interim_response))

    except Exception as e:
        print(f"Deepgram message handling error: {e}")
        error_response = {
            "type": "error",
            "message": f"Transcription error: {e}"
        }
        try:
            await client_websocket.send_text(json.dumps(error_response))
        except Exception as send_error:
            print(f"Failed to send error response: {send_error}")

    # If the session completes but we have no transcript, it was likely unclear audio
    if not full_transcript.strip():
        return "[AUDIO_UNCLEAR]"

    return full_transcript.strip()


async def send_close_stream(deepgram_websocket):
    """
    Send CloseStream signal to Deepgram
    """
    try:
        print("🔄 About to send CloseStream to Deepgram...")
        await deepgram_websocket.send(json.dumps({"type": "CloseStream"}))
        print("✅ Sent CloseStream to Deepgram successfully")
        print("⏳ Waiting for Deepgram to send final transcription results...")
        # Give Deepgram time to process and send final results
        await asyncio.sleep(1.0)
        print("⏰ CloseStream wait period completed")
    except Exception as e:
        print(f"❌ Error sending CloseStream: {e}")
        import traceback
        print(f"🔍 CloseStream error traceback: {traceback.format_exc()}")


# Track total audio sent for debugging
total_audio_bytes_sent = 0

async def forward_audio_chunk(deepgram_websocket, audio_chunk):
    """
    Forward audio chunk to Deepgram
    """
    global total_audio_bytes_sent
    try:
        await deepgram_websocket.send(audio_chunk)
        total_audio_bytes_sent += len(audio_chunk)
        print(f"Forwarded audio chunk: {len(audio_chunk)} bytes (total: {total_audio_bytes_sent} bytes)")
    except Exception as e:
        error_msg = str(e)
        print(f"Error forwarding audio to Deepgram: {error_msg}")
        print(f"📊 Total audio sent before error: {total_audio_bytes_sent} bytes")
        # Don't raise on normal connection closures (code 1000/1001)
        if "1000" not in error_msg and "1001" not in error_msg and "received 1000" not in error_msg:
            print(f"⚠️  Re-raising non-normal Deepgram error: {error_msg}")
            raise
        else:
            print(f"🔇 Ignoring normal Deepgram closure: {error_msg}")

def reset_audio_counter():
    """Reset the audio byte counter for new recording session"""
    global total_audio_bytes_sent
    print(f"🔄 Resetting audio counter (was {total_audio_bytes_sent} bytes)")
    total_audio_bytes_sent = 0
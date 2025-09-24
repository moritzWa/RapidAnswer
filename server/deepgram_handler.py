import json
import ssl
import websockets
import asyncio
from fastapi import WebSocket


async def create_deepgram_connection(deepgram_url: str, api_key: str):
    """
    Create a WebSocket connection to Deepgram for real-time transcription
    """
    uri = f"{deepgram_url}?model=nova-2&smart_format=true&encoding=linear16&sample_rate=16000&channels=1&interim_results=true"

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


async def handle_deepgram_messages(deepgram_websocket, client_websocket, ai_response_handler):
    """
    Handle incoming messages from Deepgram WebSocket and forward to client
    """
    final_transcript = ""
    session_completed = False  # Flag to prevent processing multiple final results

    try:
        async for message in deepgram_websocket:
            data = json.loads(message)

            if data.get("type") == "Results":
                # Deepgram sends alternatives ranked by confidence (best first)
                alternatives = data.get("channel", {}).get("alternatives", [])

                if alternatives:
                    # Use the most confident transcription
                    first_alt = alternatives[0]
                    transcript = first_alt.get("transcript", "")
                    confidence = first_alt.get("confidence", "N/A")

                    # Only log meaningful transcripts or issues
                    if transcript or data.get("is_final"):
                        print(f"🔍 {'FINAL' if data.get('is_final') else 'Interim'}: '{transcript}' (confidence: {confidence})")

                if transcript:
                    if data.get("is_final"):
                        if session_completed:
                            # Deepgram sometimes sends additional empty finals after real transcription
                            print(f"🔄 Ignoring additional final result after session completed")
                            continue

                        session_completed = True  # Mark session as completed

                        # Accumulate final transcript for AI response
                        final_transcript += transcript + " "

                        # Trigger AI response with complete transcript
                        if final_transcript.strip():
                            print(f"Final transcription: {final_transcript.strip()}")
                            await ai_response_handler(final_transcript.strip(), client_websocket)

                            # Reset for next turn
                            final_transcript = ""

                    else:
                        # Forward interim result to client immediately
                        interim_response = {
                            "type": "interim_transcription",
                            "text": transcript
                        }
                        await client_websocket.send_text(json.dumps(interim_response))
                else:
                    if data.get("is_final"):
                        if session_completed:
                            print(f"🔄 Ignoring additional empty final result after session completed")
                            continue

                        session_completed = True  # Mark session as completed

                        print(f"⚠️ FINAL result received but transcript is empty! Sending fallback message to AI")
                        # Send fallback message to AI instead of hanging
                        await ai_response_handler("[AUDIO_UNCLEAR]", client_websocket)

                        # Reset for next turn
                        final_transcript = ""
                    else:
                        print(f"⚠️ Interim result received but transcript is empty")
            else:
                # Only log non-Results if it's not just metadata
                if data.get("type") != "Metadata":
                    print(f"🔍 Non-Results message type: {data.get('type')}")

    except Exception as e:
        print(f"Deepgram message handling error: {e}")
        # Don't send error to client for normal connection closures
        if "1000" not in str(e):
            error_response = {
                "type": "error",
                "message": f"Transcription error: {e}"
            }
            try:
                await client_websocket.send_text(json.dumps(error_response))
            except:
                pass


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
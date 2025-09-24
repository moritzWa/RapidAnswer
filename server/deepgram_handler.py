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

    try:
        async for message in deepgram_websocket:
            data = json.loads(message)

            if data.get("type") == "Results":
                transcript = data.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")

                if transcript:
                    if data.get("is_final"):
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
        await deepgram_websocket.send(json.dumps({"type": "CloseStream"}))
        print("Sent CloseStream to Deepgram")
    except Exception as e:
        print(f"Error sending CloseStream: {e}")


async def forward_audio_chunk(deepgram_websocket, audio_chunk):
    """
    Forward audio chunk to Deepgram
    """
    try:
        await deepgram_websocket.send(audio_chunk)
        print(f"Forwarded audio chunk: {len(audio_chunk)} bytes")
    except Exception as e:
        error_msg = str(e)
        print(f"Error forwarding audio to Deepgram: {error_msg}")
        # Don't raise on normal connection closures (code 1000/1001)
        if "1000" not in error_msg and "1001" not in error_msg and "received 1000" not in error_msg:
            print(f"⚠️  Re-raising non-normal Deepgram error: {error_msg}")
            raise
        else:
            print(f"🔇 Ignoring normal Deepgram closure: {error_msg}")
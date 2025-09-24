import asyncio
import json
from fastapi import WebSocket
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

async def get_transcript_generator(client_websocket: WebSocket, dg_connection: DeepgramClient):
    """
    An async generator that yields transcripts as they are finalized by Deepgram.
    """
    transcript_queue = asyncio.Queue()

    async def on_message(self, result, **kwargs):
        if result.channel.alternatives[0].transcript:
            transcript = result.channel.alternatives[0].transcript
            if result.is_final:
                print(f"✅ Final transcript fragment: '{transcript}'")
                await transcript_queue.put(transcript)
            else:
                # print(f"🎤 Interim transcript fragment: '{transcript}'")
                interim_response = {
                    "type": "interim_transcription",
                    "text": transcript
                }
                await client_websocket.send_text(json.dumps(interim_response))

    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)

    try:
        while True:
            transcript = await transcript_queue.get()
            yield transcript
    except asyncio.CancelledError:
        print("Transcript generator cancelled.")
    finally:
        print("Transcript generator finished.")
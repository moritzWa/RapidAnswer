import asyncio
import json
from fastapi import WebSocket
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

async def get_transcript_generator(client_websocket: WebSocket, dg_connection: DeepgramClient, get_tts_state=None, get_ai_task=None):
    """
    An async generator that yields transcripts as they are finalized by Deepgram.
    """
    transcript_queue = asyncio.Queue()
    transcript_buffer = []  # Accumulate transcript parts
    last_transcript_time = None  # Track timing for custom fallback
    has_interrupted = False  # Prevent multiple interruptions for same audio

    async def on_message(self, result, **kwargs):
        nonlocal last_transcript_time, has_interrupted
        import time
        # Note: self and kwargs are required by Deepgram callback signature

        if result.channel.alternatives[0].transcript:
            transcript = result.channel.alternatives[0].transcript

            # Debug: Show all the flags we're getting
            print(f"🔍 DEBUG - Transcript: '{transcript}' | is_final: {result.is_final} | speech_final: {result.speech_final}")

            # Update last transcript time for fallback logic
            last_transcript_time = time.time()

            if not result.speech_final:
                # Accumulate transcript parts (both interim and final)
                if result.is_final:
                    print(f"📝 Final transcript fragment: '{transcript}'")
                    transcript_buffer.append(transcript)
                else:
                    # Send interim results to client for UI
                    full_interim = " ".join(transcript_buffer + [transcript])
                    interim_response = {
                        "type": "interim_transcription",
                        "text": full_interim.strip()
                    }
                    await client_websocket.send_text(json.dumps(interim_response))
                    
                    # Check if we should interrupt TTS playback (only once per utterance)
                    tts_playing = get_tts_state() if get_tts_state else False
                    
                    if not has_interrupted and tts_playing:
                        has_interrupted = True  # Prevent multiple interruptions
                        print("🛑 Interrupting TTS - user started speaking")
                        await client_websocket.send_text(json.dumps({
                            "type": "stop_audio_playback"
                        }))
                        
                        # Cancel the AI task on server side too
                        if get_ai_task:
                            ai_task = get_ai_task()
                            if ai_task and not ai_task.done():
                                ai_task.cancel()
            else:
                # speech_final=True: User has paused, send complete transcript
                transcript_buffer.append(transcript)
                full_transcript = " ".join(transcript_buffer).strip()
                print(f"✅ Speech final - complete transcript: '{full_transcript}'")
                await transcript_queue.put(full_transcript)
                transcript_buffer.clear()  # Reset for next turn
                has_interrupted = False  # Reset interruption flag for next utterance

    async def on_utterance_end(self, **kwargs):
        nonlocal has_interrupted
        # Note: self and kwargs are required by Deepgram callback signature
        # UtteranceEnd triggered when speech_final fails due to background noise
        if transcript_buffer:  # Only if we have accumulated transcript
            full_transcript = " ".join(transcript_buffer).strip()
            print(f"🔚 UtteranceEnd - complete transcript: '{full_transcript}'")
            await transcript_queue.put(full_transcript)
            transcript_buffer.clear()
            has_interrupted = False  # Reset interruption flag

    async def custom_timeout_fallback():
        """Fallback: If no speech_final after 3 seconds, manually trigger"""
        nonlocal has_interrupted
        import time
        while True:
            await asyncio.sleep(1)  # Check every second
            if (last_transcript_time and transcript_buffer and
                time.time() - last_transcript_time > 3.0):  # 3 second timeout
                full_transcript = " ".join(transcript_buffer).strip()
                print(f"⏰ Custom timeout fallback - complete transcript: '{full_transcript}'")
                await transcript_queue.put(full_transcript)
                transcript_buffer.clear()
                has_interrupted = False  # Reset interruption flag

    # Start background timeout task (variable stored for potential cleanup)
    _timeout_task = asyncio.create_task(custom_timeout_fallback())

    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
    dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)

    try:
        while True:
            transcript = await transcript_queue.get()
            yield transcript
    except asyncio.CancelledError:
        print("Transcript generator cancelled.")
    finally:
        print("Transcript generator finished.")
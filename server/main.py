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
    
    try:
        config = DeepgramClientOptions(options={"keepalive": "true"})
        deepgram: DeepgramClient = DeepgramClient(DEEPGRAM_API_KEY, config)
        dg_connection = deepgram.listen.asynclive.v("1")
        
        transcript_generator = get_transcript_generator(client_websocket, dg_connection)

        options = LiveOptions(
            model="nova-2",
            punctuate=True,
            language="en-US",
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            smart_format=True,
            endpointing=1000,
        )
        await dg_connection.start(options)

        async def forward_audio():
            try:
                while True:
                    message = await client_websocket.receive()
                    if message["type"] == "websocket.receive" and "bytes" in message:
                        await dg_connection.send(message["bytes"])
                    elif message["type"] == "websocket.receive" and "text" in message:
                        data = json.loads(message["text"])
                        if data.get("type") == "user_audio_end":
                            print("Client sent stop signal. Closing stream.")
                            break
            finally:
                await dg_connection.finish()

        async def handle_transcripts():
            nonlocal ai_task
            full_transcript = ""
            async for transcript_part in transcript_generator:
                full_transcript += transcript_part + " "
                
                if ai_task and not ai_task.done():
                    print("Barge-in detected. Cancelling previous AI response.")
                    ai_task.cancel()
                    await client_websocket.send_text(json.dumps({"type": "stop_audio_playback"}))
                
                ai_task = asyncio.create_task(handle_ai_response(full_transcript, client_websocket))

        await asyncio.gather(forward_audio(), handle_transcripts())

    except Exception as e:
        print(f"❌ WebSocket handler error: {e}")
    finally:
        if ai_task and not ai_task.done():
            ai_task.cancel()
        await client_websocket.close()
        print("🔌 WebSocket connection closed")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
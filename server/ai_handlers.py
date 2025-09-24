import openai
import json
from fastapi import WebSocket
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize OpenAI clients
openai.api_key = os.getenv("OPENAI_API_KEY")

async def handle_ai_response(transcription: str, client_websocket: WebSocket):
    """
    Process final transcription and generate AI response with TTS streaming
    """
    from tts_handlers import get_ai_response_with_sentence_streaming
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
        }
        await client_websocket.send_text(json.dumps(response))
        print(f"📤 Sent final voice_response to client")
        return True # Indicate success
    except Exception as e:
        print(f"❌ AI response error: {e}")
        error_response = {
            "type": "error",
            "message": f"AI response failed: {e}"
        }
        try:
            await client_websocket.send_text(json.dumps(error_response))
        except Exception as send_error:
            print(f"❌ Failed to send error response: {send_error}")
        return False # Indicate failure


async def stream_openai_response(text: str, websocket: WebSocket, sentence_handler):
    """Stream AI response and detect complete sentences"""
    full_response = ""
    sentence_buffer = ""

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

            stream_response = {
                "type": "ai_response_stream",
                "content": content,
                "is_complete": False
            }
            await websocket.send_text(json.dumps(stream_response))

            if any(punct in content for punct in ['.', '!', '?']):
                complete_sentence = sentence_buffer.strip()
                if len(complete_sentence) > 5:
                    await sentence_handler(complete_sentence)
                sentence_buffer = ""

    completion_response = {
        "type": "ai_response_stream",
        "content": "",
        "is_complete": True
    }
    await websocket.send_text(json.dumps(completion_response))

    return full_response, sentence_buffer

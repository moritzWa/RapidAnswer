import openai
import json
from fastapi import WebSocket
import os
from dotenv import load_dotenv
from pydantic import BaseModel

# Load environment variables
load_dotenv()

# Initialize OpenAI clients
openai.api_key = os.getenv("OPENAI_API_KEY")

class ResponseModificationAnalysis(BaseModel):
    needs_web_search: bool
    has_speed_request: bool
    speed_multiplier: float  # 0.5 = slower, 1.0 = normal, 1.5 = faster, etc.
    explanation: str  # Brief explanation

# Note: clean_web_search_response function removed - not needed for Exa+Groq pipeline

async def analyze_response_modifications(transcript: str) -> ResponseModificationAnalysis:
    """
    Analyze transcript for both web search needs and TTS speed modifications
    """
    try:
        from groq import Groq

        groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",  # Latest fast model for analysis (1,800 tokens/sec)
            messages=[
                {
                    "role": "system",
                    "content": """Analyze the user query and respond with ONLY a JSON object (no other text):
{
  "needs_web_search": boolean,
  "has_speed_request": boolean,
  "speed_multiplier": number,
  "explanation": "brief explanation"
}

Rules:
- needs_web_search: true for recent research, current events, live data, expert opinions
- has_speed_request: true if user mentions "slower", "faster", "speed up", "slow down"
- speed_multiplier: 0.5 for slower, 1.0 for normal, 1.5+ for faster, 2.0 for default
- explanation: 1-5 words max"""
                },
                {
                    "role": "user",
                    "content": transcript
                }
            ],
            temperature=0.1,  # Low temperature for consistent JSON
            max_tokens=200
        )

        # Parse the JSON response
        import json
        result_json = json.loads(response.choices[0].message.content)

        analysis_result = ResponseModificationAnalysis(
            needs_web_search=result_json["needs_web_search"],
            has_speed_request=result_json["has_speed_request"],
            speed_multiplier=result_json["speed_multiplier"],
            explanation=result_json["explanation"]
        )

        print(f"🔍🎛️ Groq analysis: search={analysis_result.needs_web_search}, speed={analysis_result.speed_multiplier:.1f}x")
        return analysis_result

    except Exception as e:
        print(f"⚠️ Groq analysis failed: {e}")
        return ResponseModificationAnalysis(
            needs_web_search=False,
            has_speed_request=False,
            speed_multiplier=2.0,
            explanation="Analysis failed, using defaults"
        )


async def handle_ai_response(transcription: str, client_websocket: WebSocket, chat_history: list):
    """
    Process final transcription and generate AI response with TTS streaming
    """
    from tts_handlers import get_ai_response_with_sentence_streaming
    print(f"📝 Starting AI response for: '{transcription}'")

    # Analyze transcript for both web search needs and TTS modifications
    analysis = await analyze_response_modifications(transcription)
    print(f"🎛️ Analysis: search={analysis.needs_web_search}, speed={analysis.speed_multiplier:.1f}x - {analysis.explanation}")

    try:
        ai_response = await get_ai_response_with_sentence_streaming(
            transcription,
            client_websocket,
            chat_history=chat_history,
            tts_speed=analysis.speed_multiplier,
            use_web_search=analysis.needs_web_search
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
        return ai_response # Return the response for history storage
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
        return None # Return None to indicate failure


async def stream_openai_response(text: str, websocket: WebSocket, sentence_handler, chat_history: list, use_web_search: bool = False):
    """Stream AI response and detect complete sentences"""
    full_response = ""
    sentence_buffer = ""

    if text == "[AUDIO_UNCLEAR]":
        user_message = "I didn't hear you clearly. Could you repeat that?"
        system_message = "You are a helpful assistant. The user's audio was unclear, so respond as if you didn't hear them properly. Keep responses conversational and concise."
        use_search = False
    else:
        user_message = text
        system_message = "You are a helpful assistant. Keep responses conversational and concise."
        use_search = use_web_search

    if use_search:
        print(f"🔍⚡ Using fast search (Exa + Groq) for query: '{text}'")
        # Use fast search pipeline with Exa + Groq
        from fast_search import fast_search_and_respond

        return await fast_search_and_respond(user_message, chat_history, websocket, sentence_handler)
    else:
        # Use regular Chat Completions for non-search queries with chat history
        model = "gpt-4o-mini"

        # Build messages with chat history
        messages = [{"role": "system", "content": system_message}]

        # Include full chat history for context
        if chat_history:
            messages.extend(chat_history)  # Include all messages

        messages.append({"role": "user", "content": user_message})

        stream = openai.chat.completions.create(
            model=model,
            messages=messages,
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

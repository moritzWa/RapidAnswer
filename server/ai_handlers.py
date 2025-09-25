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

def clean_web_search_response(text: str) -> str:
    """
    Remove citations, URLs, and source references from web search responses
    """
    import re

    # Remove markdown-style links [text](url)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

    # Remove standalone URLs
    text = re.sub(r'https?://[^\s\)]+', '', text)

    # Remove citation patterns like ([source.com](url))
    text = re.sub(r'\(\[([^\]]+)\]\([^\)]+\)\)', '', text)

    # Remove standalone parenthetical citations like (source.com)
    text = re.sub(r'\([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\)', '', text)

    # Clean up extra whitespace and line breaks
    text = re.sub(r'\n\s*\n', '\n', text)  # Multiple newlines to single
    text = re.sub(r'\s+', ' ', text)       # Multiple spaces to single

    return text.strip()

async def analyze_response_modifications(transcript: str) -> ResponseModificationAnalysis:
    """
    Analyze transcript for both web search needs and TTS speed modifications
    """
    try:
        response = openai.beta.chat.completions.parse(
            model="gpt-4.1-nano",
            messages=[
                {
                    "role": "system",
                    "content": "Analyze user query for: 1) Need for web search (recent research, opinions of experts, live data, current events) 2) Speech speed changes ('slower', 'faster', 'repeat slower'). Return needs_web_search=true for time-sensitive queries. Speed: 0.5=slower, 1.0=normal, 1.5+=faster. Brief explanation."
                },
                {
                    "role": "user",
                    "content": transcript
                }
            ],
            response_format=ResponseModificationAnalysis,
        )

        print(f"🔍🎛️ Response analysis: search={response.choices[0].message.parsed.needs_web_search}, speed={response.choices[0].message.parsed.speed_multiplier:.1f}x")
        return response.choices[0].message.parsed
    except Exception as e:
        print(f"⚠️ Response modification analysis failed: {e}")
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
        print(f"🔍 Using Responses API with web search for query: '{text}'")
        # Use Responses API for web search with chat history context
        client = openai.OpenAI()

        # Build context with chat history
        context_parts = [system_message]
        if chat_history:
            context_parts.append("\nPrevious conversation:")
            for msg in chat_history:  # Include all messages for context
                role = "User" if msg["role"] == "user" else "Assistant"
                context_parts.append(f"{role}: {msg['content']}")
        context_parts.append(f"\nUser: {user_message}")

        response = client.responses.create(
            model="gpt-4o",
            tools=[{"type": "web_search_preview"}],
            input="\n".join(context_parts)
        )

        # Process response, clean citations, and simulate streaming for compatibility
        full_response = clean_web_search_response(response.output_text)
        sentence_buffer = ""

        # Send response in chunks to simulate streaming
        for char in full_response:
            sentence_buffer += char

            # Send chunk
            stream_response = {
                "type": "ai_response_stream",
                "content": char,
                "is_complete": False
            }
            await websocket.send_text(json.dumps(stream_response))

            # Check for sentence boundaries
            if char in ['.', '!', '?'] and len(sentence_buffer.strip()) > 5:
                complete_sentence = sentence_buffer.strip()
                await sentence_handler(complete_sentence)
                sentence_buffer = ""

        # Send completion signal
        completion_response = {
            "type": "ai_response_stream",
            "content": "",
            "is_complete": True
        }
        await websocket.send_text(json.dumps(completion_response))

        return full_response, sentence_buffer
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

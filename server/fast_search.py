import os
import json
from dotenv import load_dotenv
from exa_py import Exa
from groq import Groq
from fastapi import WebSocket

load_dotenv()

# Initialize clients
exa_client = Exa(api_key=os.getenv("EXA_API_KEY"))
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

async def fast_search_and_respond(query: str, chat_history: list, websocket: WebSocket, sentence_handler):
    """
    Fast research pipeline using Exa search + Groq summarization
    """
    print(f"🔍⚡ Starting fast search for: '{query}'")

    try:
        # Step 1: Search with Exa
        print("🔍 Searching with Exa...")
        search_results = exa_client.search_and_contents(
            query,
            text=True,
            type="auto",
            num_results=5,  # Get top 5 results
            text_length_limit=500  # Limit text per result
        )

        # Step 2: Build context for Groq
        search_context = ""
        for i, result in enumerate(search_results.results, 1):
            search_context += f"\n--- Source {i}: {result.title} ---\n"
            search_context += f"{result.text[:400]}...\n"  # Limit length

        # Step 3: Build messages with chat history
        messages = [
            {
                "role": "system",
                "content": "You are a helpful assistant. Use the search results to provide a comprehensive, accurate answer. Keep responses conversational and concise. Cite sources when helpful."
            }
        ]

        # Add chat history for context
        if chat_history:
            messages.extend(chat_history)

        # Add current query with search context
        user_message = f"Question: {query}\n\nSearch Results:\n{search_context}\n\nBased on the search results above, please provide a comprehensive answer:"
        messages.append({"role": "user", "content": user_message})

        # Step 4: Stream response from Groq
        print("⚡ Generating response with Groq...")
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Latest high-quality model
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
            stream=True
        )

        # Stream processing
        full_response = ""
        sentence_buffer = ""

        for chunk in completion:
            if chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                full_response += content
                sentence_buffer += content

                # Send chunk to client
                stream_response = {
                    "type": "ai_response_stream",
                    "content": content,
                    "is_complete": False
                }
                await websocket.send_text(json.dumps(stream_response))

                # Check for sentence boundaries for TTS
                if any(punct in content for punct in ['.', '!', '?']):
                    complete_sentence = sentence_buffer.strip()
                    if len(complete_sentence) > 5:
                        await sentence_handler(complete_sentence)
                    sentence_buffer = ""

        # Send completion signal
        completion_response = {
            "type": "ai_response_stream",
            "content": "",
            "is_complete": True
        }
        await websocket.send_text(json.dumps(completion_response))

        print(f"⚡✅ Fast search completed: '{full_response[:50]}...'")
        return full_response, sentence_buffer

    except Exception as e:
        print(f"❌ Fast search error: {e}")
        raise Exception(f"Fast search failed: {e}")
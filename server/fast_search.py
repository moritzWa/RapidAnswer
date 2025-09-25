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

async def rewrite_query_for_search(query: str, chat_history: list) -> str:
    """
    Use Groq to rewrite a conversational query into a standalone search query.
    """
    if not chat_history:
        return query

    print("🧠 Rewriting query for search context...")

    try:
        history_str = "\n".join([f"{msg.get('role')}: {msg.get('content')}" for msg in chat_history])
        
        prompt = f"""Based on the chat history and the user's latest query, rewrite the query into a standalone, self-contained question suitable for a web search.

<chat-history>
{history_str}
</chat-history>

<latest-query>
{query}
</latest-query>

Provide ONLY the rewritten search query and nothing else.
"""
        
        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are a query rewriting expert."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,
            max_tokens=100
        )
        rewritten_query = completion.choices[0].message.content.strip()
        print(f"🔍 Rewritten query: '{rewritten_query}'")
        return rewritten_query
    except Exception as e:
        print(f"⚠️ Query rewrite failed: {e}. Using original query.")
        return query

async def fast_search_and_respond(query: str, chat_history: list, websocket: WebSocket, sentence_handler):
    """
    Fast research pipeline using Exa search + Groq summarization
    """
    # Step 1: Rewrite query for context
    search_query = await rewrite_query_for_search(query, chat_history)
    print(f"🔍⚡ Starting fast search for: '{search_query}'")

    try:
        # Step 2: Search with Exa
        print("🔍 Searching with Exa...")
        search_results = exa_client.search_and_contents(
            search_query,
            text={
                "max_chars": 500
            },
            type="auto",
            num_results=4,  # Get top 5 results
        )

        # Step 2: Build context for Groq using XML tags for clarity
        context_parts = []

        # Add chat history with XML tags
        if chat_history:
            context_parts.append("<chat-history>")
            for msg in chat_history:
                role = msg.get("role")
                content = msg.get("content")
                if role and content:
                    context_parts.append(f"<{role}>{content}</{role}>")
            context_parts.append("</chat-history>")

        # Add search results with XML tags
        context_parts.append("<exa-research>")
        for i, result in enumerate(search_results.results, 1):
            context_parts.append(f"<source id='{i}' title='{result.title}'>")
            context_parts.append(result.text)
            context_parts.append("</source>")
        context_parts.append("</exa-research>")

        # Step 3: Consolidate into a single user message
        final_context = "\n".join(context_parts)
        user_message = f"Based on the following context, please provide a comprehensive answer to the question.\n\n<context>\n{final_context}\n</context>\n\nQuestion: {query}"
        
        # Step 4: Build messages for Groq
        messages = [
            {
                "role": "system",
                "content": "You are a voice-based AI assistant. Use the provided context from <chat-history> and <exa-research> to answer the user's question. Synthesize the information into a smooth, conversational paragraph. Your response must be suitable for being spoken aloud. Do NOT include citations, references, or any markdown/formatting like '[Source 1]' or lists."
            },
            {
                "role": "user",
                "content": user_message
            }
        ]

        # Step 5: Stream response from Groq
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
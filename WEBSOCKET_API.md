# WebSocket API Documentation

This document outlines the WebSocket communication protocol for the RapidAnswer voice chat application. The server provides a single endpoint at `ws://localhost:8000/ws`.

## Communication Flow

The interaction model supports a continuous, "always-on" conversation with barge-in capabilities.

1.  **Connection**: The client establishes a long-lived WebSocket connection with the server.
2.  **Audio Streaming**: When the user starts a conversation, the client continuously streams raw PCM audio to the server as binary messages.
3.  **Server-Side Transcription**: The server uses Deepgram's streaming transcription. It detects pauses in the user's speech to identify the end of an utterance.
4.  **Turn Handling**: When an utterance is finalized, the server sends the transcript to the AI for a response. The transcript accumulates if the user continues speaking before the AI responds.
5.  **Barge-In (Interruption)**: If the user speaks while the AI is responding, the server cancels the in-progress AI/TTS task and processes the new user audio, creating a seamless barge-in experience.
6.  **Response Streaming**: The server streams the AI's text and synthesized audio back to the client in real-time.
7.  **End of Session**: The user can explicitly end the session, which sends a `user_audio_end` message and closes the connection.

---

## Client-to-Server Messages

### 1. Binary Audio Chunks

- **Type**: `Binary`
- **Content**: Raw PCM audio data (16-bit, 16kHz, single-channel).
- **When**: Sent continuously while the conversation is active.

### 2. User Audio End

- **Type**: `Text` (JSON)
- **When**: Sent when the user explicitly stops the conversation.
- **Example**: `{"type": "user_audio_end"}`

---

## Server-to-Client Messages

### 1. Interim Transcription

- **`type`**: `"interim_transcription"`
- **`text`**: `string`

### 2. AI Response Stream (Text)

- **`type`**: `"ai_response_stream"`
- **`content`**: `string`
- **`is_complete`**: `boolean`

### 3. Audio Stream (PCM)

- **`type`**: `"audio_stream_pcm"`
- **`pcm_chunk`**: `string` (base64-encoded)
- **`sample_rate`**: `number`
- **`channels`**: `number`

### 4. Final Voice Response

- **`type`**: `"voice_response"`
- **`transcription`**: `string`
- **`ai_response`**: `string`

### 5. Stop Audio Playback

- **`type`**: `"stop_audio_playback"`
- **When**: Sent during a barge-in to immediately halt client-side audio.

### 6. Error

- **`type`**: `"error"`
- **`message`**: `string`

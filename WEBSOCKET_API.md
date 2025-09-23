# WebSocket API Documentation

This document outlines the WebSocket communication protocol for the RapidAnswer voice chat application. The server provides a single endpoint at `ws://localhost:8000/ws`.

## Communication Flow

The current interaction model is sequential and follows these steps:

1.  **Connection**: The client establishes a WebSocket connection with the server.
2.  **Audio Transmission**: The user holds down the record button. The client captures raw PCM audio and sends it to the server in a stream of binary messages.
3.  **End of Speech**: The user releases the button. The client sends a final text message (`user_audio_end`) to signal that the audio stream is complete.
4.  **Server Processing (Sequential)**:
    a. The server buffers all incoming audio chunks.
    b. Once the `user_audio_end` message is received, it sends the complete audio buffer to the transcription service (Deepgram).
    c. After the final transcript is received, it is sent to the AI model (OpenAI).
5.  **Response Streaming**:
    a. As the AI model generates its response, the server streams the text back to the client sentence by sentence.
    b. For each sentence, the server synthesizes the audio and streams it back to the client in chunks, ensuring correct order and adding natural pauses.
6.  **Finalization**: The server sends a final message containing the full transcription and AI response text.

---

## Client-to-Server Messages

The client sends two types of messages to the server.

### 1. Binary Audio Chunks

Raw audio data sent as binary WebSocket frames.

- **Type**: `Binary`
- **Content**: A chunk of raw PCM audio data (16-bit, 16kHz, single-channel).
- **When**: Sent continuously while the user is holding the "Record" button.

### 2. User Audio End

A JSON object sent as a text frame to signal the end of user speech.

- **Type**: `Text`
- **Format**: `JSON`
- **When**: Sent once, after the user releases the "Record" button.

**Example**:

```json
{
  "type": "user_audio_end"
}
```

---

## Server-to-Client Messages

The server sends several types of JSON objects as text frames to the client.

### 1. Interim Transcription

Provides real-time feedback to the user as they are speaking.

- **`type`**: `"interim_transcription"`
- **`text`**: `string` - The partial, in-progress transcript.

**Example**:

```json
{
  "type": "interim_transcription",
  "text": "this is a test..."
}
```

### 2. AI Response Stream (Text)

Individual text chunks of the AI's response as they are generated.

- **`type`**: `"ai_response_stream"`
- **`content`**: `string` - A piece of the AI's response (e.g., a word or token).
- **`is_complete`**: `boolean` - `true` if this is the final text stream message for the conversation turn.

**Example**:

```json
{
  "type": "ai_response_stream",
  "content": "Hello",
  "is_complete": false
}
```

### 3. Audio Stream (PCM)

A chunk of the synthesized speech audio.

- **`type`**: `"audio_stream_pcm"`
- **`pcm_chunk`**: `string` - A base64-encoded string of a raw PCM audio chunk. Can be an empty string in the final message.
- **`sample_rate`**: `number` - The sample rate of the audio (e.g., 24000).
- **`channels`**: `number` - The number of audio channels (always 1).
- **`is_final`**: `boolean` - `true` if this is the last audio chunk for a given sentence.

**Example**:

```json
{
  "type": "audio_stream_pcm",
  "pcm_chunk": "U32DBgA ... (base64 data) ... ",
  "sample_rate": 24000,
  "channels": 1,
  "is_final": false
}
```

### 4. Final Voice Response

The complete and final message for the turn, containing the full text.

- **`type`**: `"voice_response"`
- **`transcription`**: `string` - The final, corrected transcript of the user's speech.
- **`ai_response`**: `string` - The full, complete text of the AI's response.
- **`audio`**: `string` - An empty string, as the audio has already been streamed.

**Example**:

```json
{
  "type": "voice_response",
  "transcription": "This is a test.",
  "ai_response": "Hello! This is a test response.",
  "audio": ""
}
```

### 5. Error

Sent if any part of the server-side processing fails.

- **`type`**: `"error"`
- **`message`**: `string` - A description of the error that occurred.

**Example**:

```json
{
  "type": "error",
  "message": "Transcription failed: No speech detected."
}
```

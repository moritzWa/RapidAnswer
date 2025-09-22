import React, { useState, useRef, useCallback } from "react";
import "./App.css";

interface ChatMessage {
  type: "user" | "assistant";
  content: string;
  timestamp: Date;
}

interface InterimMessage {
  type: "interim";
  content: string;
}

type RecordingState = "idle" | "recording" | "processing";

function App() {
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [interimMessage, setInterimMessage] = useState<InterimMessage | null>(
    null
  );
  const [streamingResponse, setStreamingResponse] = useState<string>("");
  const [streamingAudio, setStreamingAudio] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);

  // Helper function to play audio from base64
  const playAudioFromBase64 = (audioBase64: string) => {
    try {
      const audioData = atob(audioBase64);
      const audioArray = new Uint8Array(audioData.length);
      for (let i = 0; i < audioData.length; i++) {
        audioArray[i] = audioData.charCodeAt(i);
      }
      const audioBlob = new Blob([audioArray], { type: "audio/mpeg" });
      const audioUrl = URL.createObjectURL(audioBlob);
      const audio = new Audio(audioUrl);
      audio.play();
    } catch (error) {
      console.error("Error playing audio:", error);
    }
  };

  // Send raw PCM data directly
  const sendPCMData = (pcmData: Int16Array) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn("WebSocket not connected, reconnecting...");
      initWebSocket();
      return;
    }

    wsRef.current.send(pcmData.buffer);
  };

  // Initialize WebSocket connection
  const initWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket("ws://localhost:8000/ws");

    ws.onopen = () => {
      console.log("WebSocket connected");
      setError(null);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === "interim_transcription") {
        // Show interim transcription in real-time
        setInterimMessage({
          type: "interim",
          content: data.text,
        });
      } else if (data.type === "ai_response_stream") {
        if (data.is_complete) {
          // Streaming response complete
          setStreamingResponse("");
        } else {
          // Append streaming content
          setStreamingResponse((prev) => prev + data.content);
        }
      } else if (data.type === "audio_stream") {
        if (data.is_final) {
          // Audio streaming complete, play the accumulated audio
          const completeAudio = streamingAudio + data.audio_chunk;
          playAudioFromBase64(completeAudio);
          setStreamingAudio("");
        } else {
          // Accumulate audio chunks
          setStreamingAudio((prev) => prev + data.audio_chunk);
        }
      } else if (data.type === "voice_response") {
        // Clear interim message and streaming response
        setInterimMessage(null);
        setStreamingResponse("");

        // Add user message (transcription)
        setMessages((prev) => [
          ...prev,
          {
            type: "user",
            content: data.transcription,
            timestamp: new Date(),
          },
        ]);

        // Add assistant response
        setMessages((prev) => [
          ...prev,
          {
            type: "assistant",
            content: data.ai_response,
            timestamp: new Date(),
          },
        ]);

        // Play audio response (fallback for non-streaming)
        if (data.audio) {
          playAudioFromBase64(data.audio);
        }

        setRecordingState("idle");
      } else if (data.type === "error") {
        // Handle server errors
        setError(data.message);
        setRecordingState("idle");
        setInterimMessage(null);
        setStreamingResponse("");
        setStreamingAudio("");
      }
    };

    ws.onerror = (error) => {
      console.error("WebSocket error:", error);
      setError("Connection error");
      setRecordingState("idle");
    };

    ws.onclose = (event) => {
      console.log("WebSocket disconnected", event.code, event.reason);
      setRecordingState("idle");

      // Reconnect after 2 seconds if not a normal closure
      if (event.code !== 1000) {
        setTimeout(() => {
          if (wsRef.current?.readyState !== WebSocket.OPEN) {
            initWebSocket();
          }
        }, 2000);
      }
    };

    wsRef.current = ws;
  }, []);

  const startRecording = useCallback(async () => {
    if (recordingState !== "idle") return;

    try {
      setError(null);
      setRecordingState("recording");

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });

      // Initialize AudioContext for direct PCM capture
      if (!audioContextRef.current) {
        audioContextRef.current = new AudioContext({ sampleRate: 16000 });
      }

      // Create audio source from stream
      const source = audioContextRef.current.createMediaStreamSource(stream);
      sourceRef.current = source;

      // Create script processor for PCM data
      const processor = audioContextRef.current.createScriptProcessor(
        4096,
        1,
        1
      );
      processorRef.current = processor;

      let pcmBuffer: number[] = [];

      processor.onaudioprocess = (event) => {
        const inputBuffer = event.inputBuffer;
        const inputData = inputBuffer.getChannelData(0);

        // Accumulate PCM data
        for (let i = 0; i < inputData.length; i++) {
          pcmBuffer.push(inputData[i]);
        }

        // Send data every ~100ms (1600 samples at 16kHz)
        if (pcmBuffer.length >= 1600) {
          // Convert float32 to int16
          const pcm16 = new Int16Array(pcmBuffer.length);
          for (let i = 0; i < pcmBuffer.length; i++) {
            pcm16[i] = Math.max(-32768, Math.min(32767, pcmBuffer[i] * 32767));
          }

          sendPCMData(pcm16);
          pcmBuffer = [];
        }
      };

      // Connect audio processing pipeline
      source.connect(processor);
      processor.connect(audioContextRef.current.destination);
    } catch (err) {
      setError(
        `Failed to start recording: ${
          err instanceof Error ? err.message : "Unknown error"
        }`
      );
      setRecordingState("idle");
    }
  }, [recordingState]);

  const stopRecording = useCallback(() => {
    if (recordingState === "recording") {
      // Clean up audio processing
      if (processorRef.current) {
        processorRef.current.disconnect();
        processorRef.current = null;
      }

      if (sourceRef.current) {
        sourceRef.current.disconnect();
        sourceRef.current = null;
      }

      // Send end-of-stream signal
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "user_audio_end" }));
      }

      setRecordingState("processing");
    }
  }, [recordingState]);

  // Initialize WebSocket on component mount
  React.useEffect(() => {
    initWebSocket();
    return () => {
      // Cleanup WebSocket
      if (wsRef.current) {
        wsRef.current.close();
      }

      // Cleanup audio processing
      if (processorRef.current) {
        processorRef.current.disconnect();
      }

      if (sourceRef.current) {
        sourceRef.current.disconnect();
      }

      if (audioContextRef.current) {
        audioContextRef.current.close();
      }
    };
  }, []);

  return (
    <div className="app">
      <h1>RapidAnswer</h1>
      <p>Voice chat with AI - Press and hold to speak</p>

      <div className="chat-container">
        {messages.map((message, index) => (
          <div key={index} className={`message ${message.type}`}>
            <strong>{message.type === "user" ? "You" : "Assistant"}:</strong>
            <div>{message.content}</div>
            <small>{message.timestamp.toLocaleTimeString()}</small>
          </div>
        ))}

        {interimMessage && (
          <div className="message interim">
            <strong>You (transcribing...):</strong>
            <div>{interimMessage.content}</div>
          </div>
        )}

        {streamingResponse && (
          <div className="message assistant streaming">
            <strong>Assistant:</strong>
            <div>{streamingResponse}</div>
          </div>
        )}

        {recordingState === "processing" &&
          !interimMessage &&
          !streamingResponse && (
            <div className="message assistant">
              <strong>Assistant:</strong>
              <div>Processing...</div>
            </div>
          )}
      </div>

      <div className="controls">
        <button
          type="button"
          className={`record-button ${
            recordingState === "recording" ? "recording" : ""
          }`}
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          disabled={recordingState === "processing"}
        >
          {recordingState === "recording"
            ? "Recording..."
            : recordingState === "processing"
            ? "Processing..."
            : "Hold to Speak"}
        </button>

        {error && <div className="error">{error}</div>}
      </div>
    </div>
  );
}

export default App;

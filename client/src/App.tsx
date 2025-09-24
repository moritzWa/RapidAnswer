import React, { useCallback, useRef, useState } from "react";
import useWebSocket, { ReadyState } from 'react-use-websocket';
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
  const [error, setError] = useState<string | null>(null);

  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef<number>(0);

  // WebSocket connection using react-use-websocket
  const { sendMessage, sendJsonMessage, lastMessage, readyState } = useWebSocket(
    "ws://localhost:8000/ws",
    {
      onOpen: () => {
        console.log("🔌 WebSocket connection established");
        setError(null);
      },
      onClose: (event) => {
        console.log("❌ WebSocket connection closed:", {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean
        });
        setRecordingState("idle");
      },
      onError: (event) => {
        console.error("❌ WebSocket error:", event);
        setError("Connection error");
        setRecordingState("idle");
      },
      shouldReconnect: (closeEvent) => {
        // Reconnect unless it was a normal closure
        return closeEvent.code !== 1000;
      },
      reconnectAttempts: 10,
      reconnectInterval: 2000,
    }
  );


  // Schedule PCM chunk for precise 2x speed playback using Web Audio API timing
  const playPCMChunkScheduled = async (
    pcmBase64: string,
    sampleRate: number,
    channels: number
  ) => {
    try {
      // Initialize playback AudioContext if needed
      if (!playbackContextRef.current) {
        playbackContextRef.current = new AudioContext({ sampleRate });
        nextPlayTimeRef.current = playbackContextRef.current.currentTime;
      }

      const context = playbackContextRef.current;

      // Resume AudioContext if suspended (required in modern browsers)
      if (context.state === "suspended") {
        await context.resume();
      }

      // Decode base64 PCM data
      const pcmData = atob(pcmBase64);
      const pcmArray = new Uint8Array(pcmData.length);
      for (let i = 0; i < pcmData.length; i++) {
        pcmArray[i] = pcmData.charCodeAt(i);
      }

      // Convert bytes to 16-bit integers
      const samples = new Int16Array(pcmArray.buffer);

      // Create AudioBuffer
      const audioBuffer = context.createBuffer(
        channels,
        samples.length,
        sampleRate
      );
      const channelData = audioBuffer.getChannelData(0);

      // Convert int16 to float32 and copy to AudioBuffer
      for (let i = 0; i < samples.length; i++) {
        channelData[i] = samples[i] / 32768.0;
      }

      // Calculate chunk duration at normal speed
      const chunkDurationSeconds = samples.length / sampleRate;

      // Create buffer source (OpenAI already generated at 2x speed)
      const source = context.createBufferSource();
      source.buffer = audioBuffer;
      source.playbackRate.value = 1.0; // Normal playback - OpenAI already did 2x speed
      source.connect(context.destination);

      // Schedule playback at the precise next time
      const startTime = Math.max(context.currentTime, nextPlayTimeRef.current);

      source.start(startTime);

      // Update next play time (normal duration since OpenAI already compressed to 2x)
      nextPlayTimeRef.current = startTime + chunkDurationSeconds;
    } catch (error) {
      console.error("Error playing PCM chunk:", error);
    }
  };

  // Send raw PCM data directly
  const sendPCMData = (pcmData: Int16Array) => {
    if (readyState !== ReadyState.OPEN) {
      console.warn("WebSocket not connected, ready state:", readyState);
      // Stop recording if connection is lost
      if (recordingState === "recording") {
        setError("Connection lost during recording");
        setRecordingState("idle");
      }
      return;
    }
    sendMessage(pcmData.buffer);
  };

  // Handle incoming WebSocket messages
  React.useEffect(() => {
    if (lastMessage !== null) {
      const handleMessage = async () => {
        const data = JSON.parse(lastMessage.data);

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
        } else if (data.type === "audio_stream_pcm") {
          // Schedule PCM chunk for precise 2x speed playback
          if (data.pcm_chunk) {
            await playPCMChunkScheduled(
              data.pcm_chunk,
              data.sample_rate,
              data.channels
            );
          }
        } else if (data.type === "voice_response") {
          console.log("📄 Received voice_response, ensuring audio cleanup");

          // CRITICAL: Ensure all audio processing is stopped
          if (processorRef.current) {
            console.log("🚨 Force disconnecting lingering processor");
            processorRef.current.disconnect();
            processorRef.current = null;
          }

          if (sourceRef.current) {
            console.log("🚨 Force disconnecting lingering source");
            sourceRef.current.disconnect();
            sourceRef.current = null;
          }

          if (streamRef.current) {
            console.log("🚨 Force stopping lingering stream");
            streamRef.current.getTracks().forEach(track => track.stop());
            streamRef.current = null;
          }

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

          console.log("✅ Setting state to idle");
          setRecordingState("idle");
        } else if (data.type === "error") {
          // Handle server errors
          setError(data.message);
          setRecordingState("idle");
          setInterimMessage(null);
          setStreamingResponse("");
        }
      };

      handleMessage();
    }
  }, [lastMessage]);

  const startRecording = useCallback(async () => {
    console.log("🎤 startRecording called, current state:", recordingState);
    if (recordingState !== "idle") {
      console.log("⚠️  startRecording blocked, not idle:", recordingState);
      return;
    }

    // Check WebSocket connection before starting
    if (readyState !== ReadyState.OPEN) {
      console.log("❌ startRecording blocked, WebSocket not open:", readyState);
      setError("WebSocket not connected. Please wait and try again.");
      return;
    }

    try {
      console.log("🚀 Starting recording session");
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
      streamRef.current = stream;

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
  }, [recordingState, readyState]);

  const stopRecording = useCallback(() => {
    console.log("🛑 stopRecording called, current state:", recordingState);
    if (recordingState === "recording") {
      console.log("🧹 Cleaning up audio processing");

      // Clean up audio processing
      if (processorRef.current) {
        console.log("📢 Disconnecting audio processor");
        processorRef.current.disconnect();
        processorRef.current = null;
      }

      if (sourceRef.current) {
        console.log("🎧 Disconnecting audio source");
        sourceRef.current.disconnect();
        sourceRef.current = null;
      }

      // Stop microphone stream to release red dot
      if (streamRef.current) {
        console.log("🔴 Stopping microphone stream");
        streamRef.current.getTracks().forEach(track => track.stop());
        streamRef.current = null;
      }

      // Send end-of-stream signal
      if (readyState === ReadyState.OPEN) {
        console.log("📤 Sending user_audio_end");
        sendJsonMessage({ type: "user_audio_end" });
      }

      console.log("⏳ Setting state to processing");
      setRecordingState("processing");
    } else {
      console.log("⚠️  stopRecording called but not in recording state:", recordingState);
    }
  }, [recordingState, readyState]);

  // Test function with hardcoded audio data
  const testWithHardcodedAudio = useCallback(async () => {
    if (recordingState !== "idle") return;

    try {
      setError(null);
      setRecordingState("recording");

      // Wait for WebSocket connection if needed
      if (readyState !== ReadyState.OPEN) {
        console.warn("WebSocket not connected, waiting...");
        // Wait a bit for connection
        await new Promise((resolve) => setTimeout(resolve, 500));
      }

      // Load test PCM audio data
      const response = await fetch("/eval_data/test.pcm");
      const testPCMBuffer = await response.arrayBuffer();
      const pcmArray = new Int16Array(testPCMBuffer);

      // Send the test audio data in chunks (simulate real recording)
      const chunkSize = 1600; // Same as real recording (100ms at 16kHz)
      for (let i = 0; i < pcmArray.length; i += chunkSize) {
        const chunk = pcmArray.slice(i, i + chunkSize);
        if (readyState === ReadyState.OPEN) {
          sendMessage(chunk.buffer);
          // Small delay to simulate real-time recording
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
      }

      // Send end-of-stream signal
      if (readyState === ReadyState.OPEN) {
        sendJsonMessage({ type: "user_audio_end" });
      }

      setRecordingState("processing");
    } catch (err) {
      setError(
        `Test failed: ${err instanceof Error ? err.message : "Unknown error"}`
      );
      setRecordingState("idle");
    }
  }, [recordingState, readyState, sendMessage]);

  // Cleanup audio contexts on component unmount
  React.useEffect(() => {
    return () => {
      console.log("Cleaning up audio contexts");
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

      // Cleanup playback context
      if (playbackContextRef.current) {
        playbackContextRef.current.close();
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
        <div className="connection-status">
          Status: {readyState === ReadyState.CONNECTING && "Connecting..."}
          {readyState === ReadyState.OPEN && "Connected"}
          {readyState === ReadyState.CLOSING && "Disconnecting..."}
          {readyState === ReadyState.CLOSED && "Disconnected"}
          {readyState === ReadyState.UNINSTANTIATED && "Not started"}
        </div>

        <button
          type="button"
          className={`record-button ${
            recordingState === "recording" ? "recording" : ""
          }`}
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          disabled={recordingState === "processing" || readyState !== ReadyState.OPEN}
        >
          {recordingState === "recording"
            ? "Recording..."
            : recordingState === "processing"
            ? "Processing..."
            : readyState !== ReadyState.OPEN
            ? "Connecting..."
            : "Hold to Speak"}
        </button>

        <button
          type="button"
          className="test-button"
          onClick={testWithHardcodedAudio}
          disabled={recordingState === "processing"}
        >
          Dev: Test Input
        </button>

        {error && <div className="error">{error}</div>}
      </div>
    </div>
  );
}

export default App;

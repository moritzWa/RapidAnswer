import React, { useCallback, useState } from "react";
import useWebSocket, { ReadyState } from "react-use-websocket";
import { useAudioRecording } from './hooks/useAudioRecording';
import { useAudioPlayback } from './hooks/useAudioPlayback';
import { sendTestAudio } from './utils/testUtils';

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

  // WebSocket connection using react-use-websocket
  const { sendMessage, sendJsonMessage, lastMessage, readyState } =
    useWebSocket("ws://localhost:8000/ws", {
      onOpen: () => {
        console.log("🔌 WebSocket connection established");
        setError(null);
      },
      onClose: (event) => {
        console.log("❌ WebSocket connection closed:", {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean,
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
    });

  // Audio playback hook
  const { playPCMChunkScheduled, cleanup: cleanupPlayback } = useAudioPlayback();

  // Audio recording hook
  const {
    startRecording,
    stopRecording,
    forceCleanupAudio,
    cleanup: cleanupRecording
  } = useAudioRecording({
    recordingState,
    setRecordingState,
    readyState,
    setError,
    sendMessage,
    sendJsonMessage,
  });

  // Handle incoming WebSocket messages
  React.useEffect(() => {
    if (lastMessage !== null) {
      const handleMessage = async () => {
        const data = JSON.parse(lastMessage.data);

        switch (data.type) {
          case "interim_transcription":
            // Show interim transcription in real-time
            setInterimMessage({
              type: "interim",
              content: data.text,
            });
            break;

          case "ai_response_stream":
            if (data.is_complete) {
              // Streaming response complete
              setStreamingResponse("");
            } else {
              // Append streaming content
              setStreamingResponse((prev) => prev + data.content);
            }
            break;

          case "audio_stream_pcm":
            // Schedule PCM chunk for precise 2x speed playback
            if (data.pcm_chunk) {
              await playPCMChunkScheduled(
                data.pcm_chunk,
                data.sample_rate,
                data.channels
              );
            }
            break;

          case "voice_response":
            console.log("📄 Received voice_response, ensuring audio cleanup");

            // CRITICAL: Ensure all audio processing is stopped
            forceCleanupAudio();

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
            break;

          case "error":
            // Handle server errors
            setError(data.message);
            setRecordingState("idle");
            setInterimMessage(null);
            setStreamingResponse("");
            break;

          default:
            console.warn("Unknown message type:", data.type);
        }
      };

      handleMessage();
    }
  }, [lastMessage]);

  // Test function with hardcoded audio data
  const testWithHardcodedAudio = useCallback(async () => {
    await sendTestAudio({
      recordingState,
      readyState,
      sendMessage,
      sendJsonMessage,
      setRecordingState,
      setError,
    });
  }, [recordingState, readyState, sendMessage, sendJsonMessage]);

  // Add demo chat messages
  const addDemoChats = useCallback(() => {
    const demoMessages: ChatMessage[] = [
      {
        type: "user",
        content: "Hello, can you help me with JavaScript?",
        timestamp: new Date(Date.now() - 5 * 60000), // 5 minutes ago
      },
      {
        type: "assistant",
        content: "Of course! I'd be happy to help you with JavaScript. What specific topic or problem are you working on?",
        timestamp: new Date(Date.now() - 4 * 60000), // 4 minutes ago
      },
      {
        type: "user",
        content: "I'm trying to understand async/await and promises.",
        timestamp: new Date(Date.now() - 3 * 60000), // 3 minutes ago
      },
      {
        type: "assistant",
        content: "Great question! Async/await is syntactic sugar over promises that makes asynchronous code easier to read and write. Here's how they work together:\n\nPromises represent a value that will be available in the future. Async/await lets you write asynchronous code that looks more like synchronous code.",
        timestamp: new Date(Date.now() - 2 * 60000), // 2 minutes ago
      },
      {
        type: "user",
        content: "That makes sense! Can you show me an example?",
        timestamp: new Date(Date.now() - 1 * 60000), // 1 minute ago
      },
      {
        type: "assistant",
        content: "Sure! Here's a simple example:\n\n```javascript\n// Using async/await\nasync function fetchUserData(id) {\n  try {\n    const response = await fetch(`/api/users/${id}`);\n    const userData = await response.json();\n    return userData;\n  } catch (error) {\n    console.error('Error:', error);\n  }\n}\n```\n\nThis is much cleaner than chaining .then() calls!",
        timestamp: new Date(), // Now
      }
    ];

    setMessages(demoMessages);
  }, []);

  // Cleanup audio contexts on component unmount
  React.useEffect(() => {
    return () => {
      console.log("🧹 Cleaning up audio contexts");
      cleanupRecording();
      cleanupPlayback();
    };
  }, []);

  return (
    <div className="h-screen flex flex-col">
      {/* Navbar */}
      <nav className="flex justify-between items-center p-4 border-b">
        <h1 className="text-xl font-semibold">RapidAnswer</h1>
        <span className="text-sm text-gray-600">
          {readyState === ReadyState.CONNECTING && "Connecting..."}
          {readyState === ReadyState.OPEN && "Connected"}
          {readyState === ReadyState.CLOSING && "Disconnecting..."}
          {readyState === ReadyState.CLOSED && "Disconnected"}
          {readyState === ReadyState.UNINSTANTIATED && "Not started"}
        </span>
      </nav>

      {/* Full-page chat area */}
      <main className="flex-1 p-4 overflow-y-auto">
        {messages.length === 0 && !interimMessage && !streamingResponse && recordingState === "idle" && (
          <div className="h-full flex items-center justify-center text-gray-500">
            <p>Voice chat with AI - Press and hold to speak</p>
          </div>
        )}

        {messages.map((message, index) => (
          <div key={index} className={`mb-4 p-3 rounded ${
            message.type === "user"
              ? "bg-blue-50 ml-8"
              : "bg-gray-50 mr-8"
          }`}>
            <div className="font-semibold text-sm mb-1">
              {message.type === "user" ? "You" : "Assistant"}
            </div>
            <div className="whitespace-pre-wrap">{message.content}</div>
            <div className="text-xs text-gray-500 mt-1">
              {message.timestamp.toLocaleTimeString()}
            </div>
          </div>
        ))}

        {interimMessage && (
          <div className="mb-4 p-3 rounded bg-blue-100 ml-8 opacity-70">
            <div className="font-semibold text-sm mb-1">You (transcribing...)</div>
            <div>{interimMessage.content}</div>
          </div>
        )}

        {streamingResponse && (
          <div className="mb-4 p-3 rounded bg-gray-100 mr-8">
            <div className="font-semibold text-sm mb-1">Assistant</div>
            <div className="whitespace-pre-wrap">{streamingResponse}</div>
          </div>
        )}

        {recordingState === "processing" &&
          !interimMessage &&
          !streamingResponse && (
            <div className="mb-4 p-3 rounded bg-gray-100 mr-8">
              <div className="font-semibold text-sm mb-1">Assistant</div>
              <div>Processing...</div>
            </div>
          )}
      </main>

      {/* Bottom controls */}
      <footer className="p-4 border-t flex gap-3 justify-center flex-wrap">
        <button
          type="button"
          className={`px-6 py-3 rounded font-medium ${
            recordingState === "recording"
              ? "bg-red-500 text-white"
              : "bg-blue-500 text-white hover:bg-blue-600"
          } disabled:bg-gray-300 disabled:cursor-not-allowed`}
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
          className="px-4 py-2 border rounded hover:bg-gray-50 disabled:bg-gray-100 disabled:cursor-not-allowed"
          onClick={testWithHardcodedAudio}
          disabled={recordingState === "processing"}
        >
          Dev: Test Input
        </button>

        <button
          type="button"
          className="px-4 py-2 border rounded hover:bg-gray-50"
          onClick={addDemoChats}
        >
          Add Demo Chat
        </button>

        {error && (
          <div className="w-full mt-2 p-3 bg-red-50 text-red-700 rounded text-center">
            {error}
          </div>
        )}
      </footer>
    </div>
  );
}

export default App;
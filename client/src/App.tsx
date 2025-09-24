import React, { useCallback, useEffect, useRef, useState } from "react";
import useWebSocket, { ReadyState } from "react-use-websocket";
import Chat from "./db/Chat";
import Message from "./db/Message";
import { useDatabase } from "./hooks/DatabaseProvider";
import { useAudioPlayback } from "./hooks/useAudioPlayback";
import { useAudioRecording } from "./hooks/useAudioRecording";
import { sendTestAudio } from "./utils/testUtils";

interface ChatMessage {
  id: string;
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
  const database = useDatabase();
  const [activeChat, setActiveChat] = useState<Chat | null>(null);
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [interimMessage, setInterimMessage] = useState<InterimMessage | null>(
    null
  );
  const [streamingResponse, setStreamingResponse] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [recordingChat, setRecordingChat] = useState<Chat | null>(null);
  const [isConversationActive, setIsConversationActive] = useState(false);

  // Refs to hold the latest chat states to avoid useEffect dependency cycles
  const activeChatRef = useRef(activeChat);
  activeChatRef.current = activeChat;
  const recordingChatRef = useRef(recordingChat);
  recordingChatRef.current = recordingChat;

  const handleNewChat = useCallback(async () => {
    await database.write(async () => {
      const newChat = await database.collections
        .get<Chat>("chats")
        .create(() => {});
      setActiveChat(newChat as Chat);
    });
  }, [database]);

  const handleDeleteChatContent = useCallback(async () => {
    if (!activeChat) return;

    await database.write(async () => {
      const messagesToDelete = await activeChat.messages.fetch();
      const deleted = messagesToDelete.map((m: Message) =>
        m.prepareDestroyPermanently()
      );
      await database.batch(...deleted);
    });
  }, [database, activeChat]);

  const handleDeleteAllChats = useCallback(async () => {
    await database.write(async () => {
      await database.unsafeResetDatabase();
    });
    handleNewChat();
  }, [database, handleNewChat]);

  // Subscribe to chats
  useEffect(() => {
    const chatsCollection = database.collections.get<Chat>("chats");
    const subscription = chatsCollection
      .query()
      .observe()
      .subscribe((newChats: Chat[]) => {
        setChats(newChats);
        if (!activeChat && newChats.length > 0) {
          setActiveChat(newChats[0]);
        } else if (activeChat === null && newChats.length === 0) {
          handleNewChat();
        }
      });

    return () => subscription.unsubscribe();
  }, [database, activeChat, handleNewChat]);

  // Clear transient states when switching chats
  useEffect(() => {
    setInterimMessage(null);
    setStreamingResponse("");
  }, [activeChat]);

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
        return closeEvent.code !== 1000;
      },
      reconnectAttempts: 10,
      reconnectInterval: 2000,
    });

  const {
    playPCMChunkScheduled,
    cleanup: cleanupPlayback,
    stopPlayback,
  } = useAudioPlayback();

  const {
    startRecording,
    stopRecording,
    forceCleanupAudio,
    cleanup: cleanupRecording,
  } = useAudioRecording({
    recordingState,
    setRecordingState,
    readyState,
    setError,
    sendMessage,
    sendJsonMessage,
  });

  const handleChatSwitch = useCallback(
    (chat: Chat | null) => {
      if (recordingState !== "idle") {
        forceCleanupAudio();
        setRecordingState("idle");
      }
      setActiveChat(chat);
    },
    [recordingState, forceCleanupAudio]
  );

  React.useEffect(() => {
    if (lastMessage !== null) {
      const handleMessage = async () => {
        const data = JSON.parse(lastMessage.data);

        switch (data.type) {
          case "interim_transcription":
            setInterimMessage({
              type: "interim",
              content: data.text,
            });
            break;

          case "stop_audio_playback":
            console.log(" Muting audio playback due to interruption");
            stopPlayback();
            break;

          case "ai_response_stream":
            if (data.is_complete) {
              setStreamingResponse("");
            } else {
              setStreamingResponse((prev) => prev + data.content);
            }
            break;

          case "audio_stream_pcm":
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
            forceCleanupAudio();
            setInterimMessage(null);
            setStreamingResponse("");

            const targetChat =
              recordingChatRef.current || activeChatRef.current;
            if (targetChat) {
              await database.write(async () => {
                const messagesCollection = database.collections.get("messages");
                await messagesCollection.create((message: any) => {
                  message.chat.id = targetChat.id;
                  message.body = data.transcription;
                  message.isUser = true;
                });
                await messagesCollection.create((message: any) => {
                  message.chat.id = targetChat.id;
                  message.body = data.ai_response;
                  message.isUser = false;
                });
              });
            }
            setRecordingChat(null);
            // No longer set recording state to idle here; it's controlled by the toggle button
            // console.log("✅ Setting state to idle");
            // setRecordingState("idle");
            break;

          case "error":
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
  }, [lastMessage, database, forceCleanupAudio, stopPlayback]);

  const testWithHardcodedAudio = useCallback(async () => {
    if (recordingState !== "idle") return;
    setRecordingState("processing");
    await sendTestAudio({
      readyState,
      sendMessage,
      sendJsonMessage,
      setError,
    });
  }, [recordingState, readyState, sendMessage, sendJsonMessage]);

  const handleStartRecording = useCallback(() => {
    setRecordingChat(activeChat);
    startRecording();
  }, [activeChat, startRecording]);

  const handleStopRecording = useCallback(() => {
    stopRecording();
  }, [stopRecording]);

  // Handle conversation toggle
  useEffect(() => {
    if (isConversationActive) {
      handleStartRecording();
    } else {
      handleStopRecording();
    }
  }, [isConversationActive]);

  React.useEffect(() => {
    return () => {
      console.log("🧹 Cleaning up audio contexts");
      cleanupRecording();
      cleanupPlayback();
    };
  }, [cleanupRecording, cleanupPlayback]);

  return (
    <div className="h-screen flex flex-col">
      <nav className="flex justify-between items-center p-4 border-b">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-semibold">RapidAnswer</h1>
          <select
            value={activeChat?.id || ""}
            onChange={(e) => {
              const chat = chats.find((c) => c.id === e.target.value);
              handleChatSwitch(chat || null);
            }}
            className="border p-1 h-8 rounded"
          >
            {chats.map((chat) => (
              <option key={chat.id} value={chat.id}>
                Chat from {new Date(chat.createdAt).toLocaleString()}
              </option>
            ))}
          </select>
          <button
            onClick={() => {
              if (recordingState !== "idle") {
                forceCleanupAudio();
                setRecordingState("idle");
              }
              setInterimMessage(null);
              setStreamingResponse("");
              handleNewChat();
            }}
            className="border h-8 w-8 rounded text-xl"
          >
            +
          </button>
        </div>
        <span className="text-sm text-gray-600">
          {readyState === ReadyState.CONNECTING && "Connecting..."}
          {readyState === ReadyState.OPEN && "Connected"}
          {readyState === ReadyState.CLOSING && "Disconnecting..."}
          {readyState === ReadyState.CLOSED && "Disconnected"}
          {readyState === ReadyState.UNINSTANTIATED && "Not started"}
        </span>
      </nav>

      <main className="flex-1 p-4 overflow-y-auto">
        {activeChat && (
          <MessagesList
            activeChat={activeChat}
            interimMessage={interimMessage}
            streamingResponse={streamingResponse}
            recordingState={recordingState}
          />
        )}
      </main>

      <footer className="p-4 border-t flex gap-3 justify-center flex-wrap">
        <button
          type="button"
          className={`px-6 py-3 rounded font-medium ${
            isConversationActive
              ? "bg-red-500 text-white"
              : "bg-green-500 text-white hover:bg-green-600"
          } disabled:bg-gray-300 disabled:cursor-not-allowed`}
          onClick={() => setIsConversationActive((prev) => !prev)}
          disabled={readyState !== ReadyState.OPEN}
        >
          {isConversationActive ? "Stop Conversation" : "Start Conversation"}
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
          className="px-4 py-2 border rounded bg-yellow-100 hover:bg-yellow-200 disabled:bg-gray-100 disabled:cursor-not-allowed"
          onClick={handleDeleteChatContent}
          disabled={!activeChat}
        >
          Delete Chat Content
        </button>

        <button
          type="button"
          className="px-4 py-2 border rounded bg-red-100 hover:bg-red-200"
          onClick={handleDeleteAllChats}
        >
          Delete All Chats
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

function MessagesList({
  activeChat,
  interimMessage,
  streamingResponse,
  recordingState,
}: {
  activeChat: Chat;
  interimMessage: InterimMessage | null;
  streamingResponse: string;
  recordingState: RecordingState;
}) {
  const [messages, setMessages] = useState<Message[]>([]);

  useEffect(() => {
    if (!activeChat) return;

    const subscription = activeChat.messages
      .observe()
      .subscribe((newMessages: Message[]) => {
        setMessages(newMessages);
      });

    return () => subscription.unsubscribe();
  }, [activeChat]);

  const formattedMessages = messages
    .map(
      (message): ChatMessage => ({
        id: message.id,
        type: message.isUser ? "user" : "assistant",
        content: message.body,
        timestamp: message.createdAt,
      })
    )
    .sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime());

  if (
    formattedMessages.length === 0 &&
    !interimMessage &&
    !streamingResponse &&
    recordingState === "idle"
  ) {
    return (
      <div className="h-full flex items-center justify-center text-gray-500">
        <p>Voice chat with AI - Press and hold to speak</p>
      </div>
    );
  }

  return (
    <>
      {formattedMessages.map((message) => (
        <div
          key={message.id}
          className={`mb-4 p-3 rounded ${
            message.type === "user" ? "bg-blue-50 ml-8" : "bg-gray-50 mr-8"
          }`}
        >
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
          <div className="font-semibold text-sm mb-1">
            You (transcribing...)
          </div>
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
    </>
  );
}

export default App;

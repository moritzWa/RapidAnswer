import { useCallback, useRef } from "react";
import { ReadyState } from "react-use-websocket";

type RecordingState = "idle" | "recording" | "processing";

interface UseAudioRecordingProps {
  recordingState: RecordingState;
  setRecordingState: (state: RecordingState) => void;
  readyState: ReadyState;
  setError: (error: string | null) => void;
  sendMessage: (message: ArrayBuffer) => void;
  sendJsonMessage: (message: any) => void;
}

export function useAudioRecording({
  recordingState,
  setRecordingState,
  readyState,
  setError,
  sendMessage,
  sendJsonMessage,
}: UseAudioRecordingProps) {
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

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
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }

      // Send end-of-stream signal
      if (readyState === ReadyState.OPEN) {
        console.log("📤 Sending user_audio_end");
        sendJsonMessage({ type: "user_audio_end" });
      }

      console.log("✅ Setting state to idle");
      setRecordingState("idle");
    } else {
      console.log(
        "⚠️  stopRecording called but not in recording state:",
        recordingState
      );
    }
  }, [recordingState, readyState]);

  const forceCleanupAudio = useCallback(() => {
    console.log("🚨 Force cleaning audio processing");

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
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  const cleanup = useCallback(() => {
    console.log("🧹 Audio recording cleanup on unmount");

    if (processorRef.current) {
      processorRef.current.disconnect();
    }

    if (sourceRef.current) {
      sourceRef.current.disconnect();
    }

    if (audioContextRef.current) {
      audioContextRef.current.close();
    }
  }, []);

  return {
    startRecording,
    stopRecording,
    forceCleanupAudio,
    cleanup,
  };
}

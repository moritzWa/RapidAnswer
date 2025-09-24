import { ReadyState } from 'react-use-websocket';

export interface TestAudioParams {
  recordingState: string;
  readyState: ReadyState;
  sendMessage: (message: ArrayBuffer) => void;
  sendJsonMessage: (message: any) => void;
  setRecordingState: (state: "idle" | "recording" | "processing") => void;
  setError: (error: string | null) => void;
}

export const sendTestAudio = async (params: TestAudioParams): Promise<void> => {
  const {
    recordingState,
    readyState,
    sendMessage,
    sendJsonMessage,
    setRecordingState,
    setError
  } = params;

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
};
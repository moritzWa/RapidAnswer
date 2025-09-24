import { useCallback, useRef } from 'react';

export function useAudioPlayback() {
  const playbackContextRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef<number>(0);

  // Schedule PCM chunk for precise 2x speed playback using Web Audio API timing
  const playPCMChunkScheduled = useCallback(async (
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
  }, []);

  // Cleanup on unmount
  const cleanup = useCallback(() => {
    console.log("🧹 Audio playback cleanup on unmount");

    if (playbackContextRef.current) {
      playbackContextRef.current.close();
    }
  }, []);

  return {
    playPCMChunkScheduled,
    cleanup,
  };
}
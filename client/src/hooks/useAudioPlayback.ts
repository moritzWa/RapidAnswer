import { useCallback, useRef } from "react";

export function useAudioPlayback() {
  const playbackContextRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef<number>(0);
  const activeSourcesRef = useRef<AudioBufferSourceNode[]>([]);

  // Schedule PCM chunk for precise 2x speed playback using Web Audio API timing
  const playPCMChunkScheduled = useCallback(
    async (pcmBase64: string, sampleRate: number, channels: number) => {
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
        const startTime = Math.max(
          context.currentTime,
          nextPlayTimeRef.current
        );

        source.start(startTime);

        // Track this source so we can stop it if interrupted
        activeSourcesRef.current.push(source);

        // Remove from tracking when it finishes naturally
        source.onended = () => {
          const index = activeSourcesRef.current.indexOf(source);
          if (index > -1) {
            activeSourcesRef.current.splice(index, 1);
          }
        };

        // Update next play time (normal duration since OpenAI already compressed to 2x)
        nextPlayTimeRef.current = startTime + chunkDurationSeconds;
      } catch (error) {
        console.error("Error playing PCM chunk:", error);
      }
    },
    []
  );

  // Cleanup on unmount
  const cleanup = useCallback(() => {
    console.log("🧹 Audio playback cleanup on unmount");

    // Stop any remaining scheduled audio
    activeSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch (e) {
        // Source might have already finished, that's ok
      }
    });
    activeSourcesRef.current = [];

    if (playbackContextRef.current) {
      playbackContextRef.current.close();
      playbackContextRef.current = null;
    }
  }, []);

  const stopPlayback = useCallback(() => {
    console.log("🛑 Stopping audio playback");

    // Stop all scheduled audio chunks immediately
    activeSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch (e) {
        // Source might have already finished, that's ok
      }
    });
    activeSourcesRef.current = [];

    // Reset the next play time so new audio starts immediately
    nextPlayTimeRef.current = 0;

    // Close the audio context
    if (playbackContextRef.current) {
      playbackContextRef.current.close();
      playbackContextRef.current = null;
    }
  }, []);

  return {
    playPCMChunkScheduled,
    cleanup,
    stopPlayback,
  };
}

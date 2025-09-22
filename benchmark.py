#!/usr/bin/env python3
"""
Benchmark script to measure voice chat response times
"""
import asyncio
import json
import time
import websockets
import statistics
from typing import List, Dict

class VoiceChatBenchmark:
    def __init__(self, websocket_url: str = "ws://localhost:8000/ws"):
        self.websocket_url = websocket_url
        self.pcm_file_path = "client/eval_data/new-version-fast.pcm"

    async def load_test_audio(self) -> bytes:
        """Load the test PCM audio file"""
        try:
            with open(self.pcm_file_path, 'rb') as f:
                return f.read()
        except FileNotFoundError:
            print(f"❌ Test audio file not found: {self.pcm_file_path}")
            return b""

    async def run_single_test(self) -> Dict[str, float]:
        """Run a single benchmark test and return timing data"""
        audio_data = await self.load_test_audio()
        if not audio_data:
            return {}

        results = {
            "upload_time": 0.0,
            "transcription_time": 0.0,
            "ai_response_time": 0.0,
            "tts_time": 0.0,
            "total_time": 0.0
        }

        start_time = time.time()
        upload_start = start_time
        transcription_start = None
        ai_response_start = None
        tts_start = None

        try:
            async with websockets.connect(self.websocket_url) as websocket:
                print("🔌 Connected to WebSocket")

                # Send audio data in chunks (simulate real recording)
                chunk_size = 3200  # Same as client
                for i in range(0, len(audio_data), chunk_size):
                    chunk = audio_data[i:i + chunk_size]
                    await websocket.send(chunk)
                    # Small delay to simulate real-time
                    await asyncio.sleep(0.1)

                # Mark upload complete
                upload_end = time.time()
                results["upload_time"] = upload_end - upload_start

                # Send end signal
                await websocket.send(json.dumps({"type": "user_audio_end"}))
                print(f"📤 Audio uploaded in {results['upload_time']:.2f}s")

                # Wait for responses and track timing
                transcription_received = False
                ai_response_complete = False
                tts_complete = False

                async for message in websocket:
                    data = json.loads(message)
                    current_time = time.time()

                    if data.get("type") == "interim_transcription":
                        if transcription_start is None:
                            transcription_start = current_time
                            print("🎤 Transcription started")

                    elif data.get("type") == "ai_response_stream":
                        if ai_response_start is None:
                            ai_response_start = current_time
                            results["transcription_time"] = current_time - upload_end
                            print(f"📝 Transcription completed in {results['transcription_time']:.2f}s")

                        if data.get("is_complete"):
                            ai_response_complete = True
                            results["ai_response_time"] = current_time - ai_response_start
                            print(f"🤖 AI response completed in {results['ai_response_time']:.2f}s")

                    elif data.get("type") == "audio_stream_pcm":
                        if tts_start is None:
                            tts_start = current_time
                            print("🔊 TTS started")

                        if data.get("is_final"):
                            tts_complete = True
                            results["tts_time"] = current_time - tts_start
                            print(f"🎵 TTS completed in {results['tts_time']:.2f}s")
                            break

                    elif data.get("type") == "voice_response":
                        # Legacy response format
                        end_time = current_time
                        results["total_time"] = end_time - start_time
                        if transcription_start is None:
                            results["transcription_time"] = end_time - upload_end
                        print(f"✅ Complete response in {results['total_time']:.2f}s")
                        break

                    elif data.get("type") == "error":
                        print(f"❌ Error: {data.get('message')}")
                        break

                # Calculate total time
                results["total_time"] = time.time() - start_time

        except Exception as e:
            print(f"❌ Benchmark failed: {e}")
            return {}

        return results

    async def run_benchmark(self, num_tests: int = 5) -> Dict[str, List[float]]:
        """Run multiple tests and collect statistics"""
        print(f"\n🚀 Running {num_tests} benchmark tests...\n")

        all_results = {
            "upload_time": [],
            "transcription_time": [],
            "ai_response_time": [],
            "tts_time": [],
            "total_time": []
        }

        for i in range(num_tests):
            print(f"\n--- Test {i + 1}/{num_tests} ---")
            result = await self.run_single_test()

            if result:
                for key in all_results:
                    if key in result:
                        all_results[key].append(result[key])

            # Wait between tests
            if i < num_tests - 1:
                print("⏳ Waiting 2s before next test...")
                await asyncio.sleep(2)

        return all_results

    def print_statistics(self, results: Dict[str, List[float]]):
        """Print benchmark statistics"""
        print("\n" + "="*50)
        print("📊 BENCHMARK RESULTS")
        print("="*50)

        for metric, times in results.items():
            if times:
                avg = statistics.mean(times)
                median = statistics.median(times)
                min_time = min(times)
                max_time = max(times)
                std_dev = statistics.stdev(times) if len(times) > 1 else 0

                print(f"\n{metric.replace('_', ' ').title()}:")
                print(f"  Average: {avg:.2f}s")
                print(f"  Median:  {median:.2f}s")
                print(f"  Min:     {min_time:.2f}s")
                print(f"  Max:     {max_time:.2f}s")
                print(f"  Std Dev: {std_dev:.2f}s")

        if results["total_time"]:
            total_avg = statistics.mean(results["total_time"])
            print(f"\n🎯 Average Total Response Time: {total_avg:.2f} seconds")

async def main():
    """Main benchmark function"""
    import argparse

    parser = argparse.ArgumentParser(description="Benchmark voice chat response times")
    parser.add_argument("--tests", "-n", type=int, default=5, help="Number of tests to run (default: 5)")
    parser.add_argument("--url", "-u", default="ws://localhost:8000/ws", help="WebSocket URL")

    args = parser.parse_args()

    benchmark = VoiceChatBenchmark(args.url)
    results = await benchmark.run_benchmark(args.tests)
    benchmark.print_statistics(results)

if __name__ == "__main__":
    asyncio.run(main())
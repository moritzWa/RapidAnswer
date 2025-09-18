# RapidAnswer

Voice chat app with real-time transcription and AI responses - like ChatGPT voice mode but simplified.

## Project Structure

```
rapidanswer/
├── client/          # React + TypeScript frontend
├── server/          # Python FastAPI backend
├── ios-app/         # Swift iOS app (future)
└── README.md
```

## Development

### Setup

1. **Install dependencies**
```bash
npm install
cd client && npm install
cd ../server && pip install -r requirements.txt
```

2. **Set up OpenAI API key**
```bash
cd server
cp .env.example .env
# Edit .env and add your OpenAI API key:
# OPENAI_API_KEY=sk-your-key-here
```

### Running locally
```bash
# Terminal 1: Start Python server (from project root)
cd server && python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Start React client (from project root)
cd client && npm run dev
```

### Testing the app
1. Open http://localhost:5173 in your browser
2. Allow microphone access when prompted
3. Hold the "Hold to Speak" button and say something
4. Release the button and wait for the AI response

## Tech Stack

- **Client**: React + TypeScript + Vite
- **Server**: Python + FastAPI + PostgreSQL
- **AI**: OpenAI Whisper (transcription) + OpenAI API (chat) + TTS
- **iOS**: Swift (future phase)

## Features (Phase 1)

- [x] Record audio in browser
- [x] Send to Python backend for processing
- [x] Transcribe with Whisper
- [x] Get AI response via OpenAI API
- [x] Convert response to speech
- [x] Play audio response in browser# RapidAnswer

import { useState, useRef, useCallback } from 'react'
import './App.css'

interface ChatMessage {
  type: 'user' | 'assistant'
  content: string
  timestamp: Date
}

function App() {
  const [isRecording, setIsRecording] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [error, setError] = useState<string | null>(null)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const startRecording = useCallback(async () => {
    try {
      setError(null)
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true
        }
      })

      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: 'audio/webm;codecs=opus'
      })

      mediaRecorderRef.current = mediaRecorder
      chunksRef.current = []

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data)
        }
      }

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(chunksRef.current, { type: 'audio/webm' })

        // Stop all tracks to free up the microphone
        stream.getTracks().forEach(track => track.stop())

        // Process the audio
        await processAudio(audioBlob)
      }

      mediaRecorder.start()
      setIsRecording(true)
    } catch (err) {
      setError(`Failed to start recording: ${err instanceof Error ? err.message : 'Unknown error'}`)
    }
  }, [])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop()
      setIsRecording(false)
    }
  }, [isRecording])

  const processAudio = async (audioBlob: Blob) => {
    setIsProcessing(true)
    setIsRecording(false) // Ensure recording state is reset
    try {
      const formData = new FormData()
      formData.append('audio', audioBlob, 'recording.webm')

      const response = await fetch('http://localhost:8000/process-voice', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`)
      }

      const result = await response.json()

      // Add user message (transcription)
      setMessages(prev => [...prev, {
        type: 'user',
        content: result.transcription,
        timestamp: new Date()
      }])

      // Add assistant response
      setMessages(prev => [...prev, {
        type: 'assistant',
        content: result.ai_response,
        timestamp: new Date()
      }])

      // Play audio response
      if (result.audio) {
        const audioData = atob(result.audio)
        const audioArray = new Uint8Array(audioData.length)
        for (let i = 0; i < audioData.length; i++) {
          audioArray[i] = audioData.charCodeAt(i)
        }
        const audioBlob = new Blob([audioArray], { type: 'audio/mpeg' })
        const audioUrl = URL.createObjectURL(audioBlob)
        const audio = new Audio(audioUrl)
        await audio.play()
      }

    } catch (err) {
      setError(`Failed to process audio: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setIsProcessing(false)
    }
  }

  return (
    <div className="app">
      <h1>RapidAnswer</h1>
      <p>Voice chat with AI - Press and hold to speak</p>

      <div className="chat-container">
        {messages.map((message, index) => (
          <div key={index} className={`message ${message.type}`}>
            <strong>{message.type === 'user' ? 'You' : 'Assistant'}:</strong>
            <div>{message.content}</div>
            <small>{message.timestamp.toLocaleTimeString()}</small>
          </div>
        ))}

        {isProcessing && (
          <div className="message assistant">
            <strong>Assistant:</strong>
            <div>Processing...</div>
          </div>
        )}
      </div>

      <div className="controls">
        <button
          type="button"
          className={`record-button ${isRecording ? 'recording' : ''}`}
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          disabled={isProcessing}
        >
          {isRecording ? 'Recording...' : isProcessing ? 'Processing...' : 'Hold to Speak'}
        </button>

        {error && (
          <div className="error">{error}</div>
        )}
      </div>
    </div>
  )
}

export default App

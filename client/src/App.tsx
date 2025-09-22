import React, { useState, useRef, useCallback } from 'react'
import './App.css'

interface ChatMessage {
  type: 'user' | 'assistant'
  content: string
  timestamp: Date
}

type RecordingState = 'idle' | 'recording' | 'processing'

function App() {
  const [recordingState, setRecordingState] = useState<RecordingState>('idle')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [error, setError] = useState<string | null>(null)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  // Initialize WebSocket connection
  const initWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket('ws://localhost:8000/ws')

    ws.onopen = () => {
      console.log('WebSocket connected')
    }

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)

      if (data.type === 'voice_response') {
        // Add user message (transcription)
        setMessages(prev => [...prev, {
          type: 'user',
          content: data.transcription,
          timestamp: new Date()
        }])

        // Add assistant response
        setMessages(prev => [...prev, {
          type: 'assistant',
          content: data.ai_response,
          timestamp: new Date()
        }])

        // Play audio response
        if (data.audio) {
          const audioData = atob(data.audio)
          const audioArray = new Uint8Array(audioData.length)
          for (let i = 0; i < audioData.length; i++) {
            audioArray[i] = audioData.charCodeAt(i)
          }
          const audioBlob = new Blob([audioArray], { type: 'audio/mpeg' })
          const audioUrl = URL.createObjectURL(audioBlob)
          const audio = new Audio(audioUrl)
          audio.play()
        }

        setRecordingState('idle')
      }
    }

    ws.onerror = (error) => {
      console.error('WebSocket error:', error)
      setError('Connection error')
    }

    ws.onclose = () => {
      console.log('WebSocket disconnected')
    }

    wsRef.current = ws
  }, [])

  const startRecording = useCallback(async () => {
    if (recordingState !== 'idle') return

    try {
      setError(null)
      setRecordingState('recording')

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
    } catch (err) {
      setError(`Failed to start recording: ${err instanceof Error ? err.message : 'Unknown error'}`)
      setRecordingState('idle')
    }
  }, [recordingState])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && recordingState === 'recording') {
      mediaRecorderRef.current.stop()
      setRecordingState('processing')
    }
  }, [recordingState])

  const processAudio = async (audioBlob: Blob) => {
    try {
      // Ensure WebSocket is connected
      initWebSocket()

      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        throw new Error('WebSocket not connected')
      }

      // Convert audio blob to base64
      const arrayBuffer = await audioBlob.arrayBuffer()
      const base64Audio = btoa(String.fromCharCode(...new Uint8Array(arrayBuffer)))

      // Send audio data via WebSocket
      const message = {
        type: 'process_voice',
        audio_data: base64Audio
      }

      wsRef.current.send(JSON.stringify(message))

    } catch (err) {
      setError(`Failed to process audio: ${err instanceof Error ? err.message : 'Unknown error'}`)
      setRecordingState('idle')
    }
  }

  // Initialize WebSocket on component mount
  React.useEffect(() => {
    initWebSocket()
    return () => {
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [])

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

        {recordingState === 'processing' && (
          <div className="message assistant">
            <strong>Assistant:</strong>
            <div>Processing...</div>
          </div>
        )}
      </div>

      <div className="controls">
        <button
          type="button"
          className={`record-button ${recordingState === 'recording' ? 'recording' : ''}`}
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          disabled={recordingState === 'processing'}
        >
          {recordingState === 'recording' ? 'Recording...' :
           recordingState === 'processing' ? 'Processing...' :
           'Hold to Speak'}
        </button>

        {error && (
          <div className="error">{error}</div>
        )}
      </div>
    </div>
  )
}

export default App

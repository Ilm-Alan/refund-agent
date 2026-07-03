import { useEffect, useRef, useState } from 'react'
import { fetchConfig, sendVoice, streamChat } from '../lib/api'

interface Message {
  role: 'customer' | 'agent'
  text: string
}

// The agent is prompted to reply in plain text, but models still emit the
// occasional **bold** span; render it rather than showing raw asterisks.
function renderInline(text: string) {
  const parts = text.split(/\*\*(.+?)\*\*/g)
  return parts.map((part, i) =>
    i % 2 === 1 ? <strong key={i}>{part}</strong> : part,
  )
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [voiceEnabled, setVoiceEnabled] = useState(false)
  const [recording, setRecording] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [level, setLevel] = useState(0)
  const sessionId = useRef(crypto.randomUUID())
  const scroller = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  // Created during the Speak click so playback is not blocked by the
  // browser's autoplay policy when the reply arrives much later.
  const audioCtx = useRef<AudioContext | null>(null)

  useEffect(() => {
    fetchConfig()
      .then((config) => setVoiceEnabled(config.voice))
      .catch(() => {})
  }, [])

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [messages, status])

  useEffect(() => {
    if (!busy) inputRef.current?.focus()
  }, [busy])

  async function send() {
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    setBusy(true)
    setMessages((m) => [...m, { role: 'customer', text }])
    setStatus('Sending')
    try {
      await streamChat(sessionId.current, text, (event) => {
        if (event.kind === 'working' && event.label) {
          setStatus(event.label)
        } else if (event.kind === 'reply' && event.text) {
          setMessages((m) => [...m, { role: 'agent', text: event.text! }])
          setStatus(null)
        }
      })
    } catch {
      setMessages((m) => [
        ...m,
        {
          role: 'agent',
          text: 'Sorry, the connection dropped. Please send that again.',
        },
      ])
    } finally {
      setStatus(null)
      setBusy(false)
    }
  }

  function watchLevel(stream: MediaStream) {
    const ctx = audioCtx.current
    if (!ctx) return () => {}
    const source = ctx.createMediaStreamSource(stream)
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 512
    source.connect(analyser)
    const samples = new Uint8Array(analyser.fftSize)
    let frame = 0
    const tick = () => {
      analyser.getByteTimeDomainData(samples)
      let sum = 0
      for (const s of samples) sum += (s - 128) ** 2
      const rms = Math.sqrt(sum / samples.length) / 128
      setLevel(Math.min(1, rms * 4))
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
      source.disconnect()
      setLevel(0)
    }
  }

  async function toggleRecording() {
    if (busy) return
    if (recording) {
      recorder.current?.stop()
      return
    }
    try {
      audioCtx.current ??= new AudioContext()
      void audioCtx.current.resume()
      setStatus('Starting microphone')
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mediaRecorder = new MediaRecorder(stream)
      chunks.current = []
      mediaRecorder.ondataavailable = (e) => chunks.current.push(e.data)
      // Cap a forgotten recording at 60 seconds.
      const stopTimer = window.setTimeout(() => {
        if (mediaRecorder.state !== 'inactive') mediaRecorder.stop()
      }, 60_000)
      let stopLevel = () => {}
      let elapsedTimer = 0
      mediaRecorder.onstart = () => {
        // Only now is audio actually being captured.
        setStatus(null)
        setRecording(true)
        setElapsed(0)
        elapsedTimer = window.setInterval(
          () => setElapsed((s) => s + 1),
          1000,
        )
        stopLevel = watchLevel(stream)
      }
      mediaRecorder.onstop = () => {
        window.clearTimeout(stopTimer)
        window.clearInterval(elapsedTimer)
        stopLevel()
        stream.getTracks().forEach((track) => track.stop())
        setRecording(false)
        const blob = new Blob(chunks.current, {
          type: mediaRecorder.mimeType || 'audio/webm',
        })
        void submitVoice(blob)
      }
      recorder.current = mediaRecorder
      mediaRecorder.start()
    } catch {
      setStatus(null)
      setMessages((m) => [
        ...m,
        {
          role: 'agent',
          text: 'Microphone access was blocked. You can keep typing instead.',
        },
      ])
    }
  }

  async function submitVoice(blob: Blob) {
    setBusy(true)
    setStatus('Listening back and checking that')
    try {
      const turn = await sendVoice(sessionId.current, blob)
      setMessages((m) => [
        ...m,
        { role: 'customer', text: turn.transcript },
        { role: 'agent', text: turn.reply },
      ])
      if (turn.audio) void playReply(turn.audio)
    } catch {
      setMessages((m) => [
        ...m,
        {
          role: 'agent',
          text: 'Sorry, I could not process that recording. Please try again.',
        },
      ])
    } finally {
      setStatus(null)
      setBusy(false)
    }
  }

  async function playReply(b64: string) {
    try {
      const ctx = audioCtx.current
      if (!ctx) return
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
      const buffer = await ctx.decodeAudioData(bytes.buffer)
      const source = ctx.createBufferSource()
      source.buffer = buffer
      source.connect(ctx.destination)
      source.start()
    } catch {
      // The reply is on screen as text either way.
    }
  }

  function startOver() {
    sessionId.current = crypto.randomUUID()
    setMessages([])
    setStatus(null)
  }

  return (
    <div className="chat-page">
      <div className="chat-column">
        <div className="chat-toolbar">
          <h1>Refunds</h1>
          <button type="button" className="linklike" onClick={startOver}>
            New conversation
          </button>
        </div>
        <div className="messages" ref={scroller}>
          {messages.length === 0 && (
            <p className="hint">
              Ask about a refund on one of your orders, by text
              {voiceEnabled ? ' or voice' : ''}. You will be asked for the
              email address on your account.
            </p>
          )}
          {messages.map((message, i) => (
            <div key={i} className={`msg ${message.role}`}>
              {renderInline(message.text)}
            </div>
          ))}
          {status && <div className="status-line">{status}</div>}
        </div>
        {recording && (
          <div className="recording-strip">
            <span className="recording-label">Recording, speak now</span>
            <span
              className="level-meter"
              role="meter"
              aria-label="Microphone level"
            >
              <span
                className="level-fill"
                style={{ width: `${Math.round(level * 100)}%` }}
              />
            </span>
            <span className="recording-time">
              0:{String(elapsed).padStart(2, '0')} / 1:00
            </span>
          </div>
        )}
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault()
            void send()
          }}
        >
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Write a message"
            disabled={busy || recording}
            aria-label="Message"
          />
          {voiceEnabled && (
            <button
              type="button"
              className={`voice-btn ${recording ? 'recording' : ''}`}
              onClick={() => void toggleRecording()}
              disabled={busy}
            >
              {recording ? 'Stop' : 'Speak'}
            </button>
          )}
          <button type="submit" disabled={busy || recording || !draft.trim()}>
            Send
          </button>
        </form>
      </div>
    </div>
  )
}

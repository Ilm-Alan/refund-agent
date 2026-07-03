import { useEffect, useRef, useState } from 'react'
import { streamChat } from '../lib/api'

interface Message {
  role: 'customer' | 'agent'
  text: string
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const sessionId = useRef(crypto.randomUUID())
  const scroller = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

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
              Ask about a refund on one of your orders. You will be asked for
              the email address on your account.
            </p>
          )}
          {messages.map((message, i) => (
            <div key={i} className={`msg ${message.role}`}>
              {message.text}
            </div>
          ))}
          {status && <div className="status-line">{status}</div>}
        </div>
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
            disabled={busy}
            aria-label="Message"
          />
          <button type="submit" disabled={busy || !draft.trim()}>
            Send
          </button>
        </form>
      </div>
    </div>
  )
}

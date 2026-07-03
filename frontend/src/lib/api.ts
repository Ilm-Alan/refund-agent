import type { ChatStreamEvent, CrmSnapshot } from '../types'

/** POST one chat turn and forward each SSE event as it arrives. */
export async function streamChat(
  sessionId: string,
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!response.ok || !response.body) {
    throw new Error(`chat request failed: ${response.status}`)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      for (const line of frame.split('\n')) {
        if (line.startsWith('data: ')) {
          onEvent(JSON.parse(line.slice(6)))
        }
      }
    }
  }
}

export interface VoiceTurn {
  transcript: string
  reply: string
  audio: string | null
}

export async function fetchConfig(): Promise<{ voice: boolean }> {
  const response = await fetch('/api/config')
  if (!response.ok) throw new Error(`config request failed: ${response.status}`)
  return response.json()
}

// MediaRecorder containers vary by browser (Chrome records webm, Safari mp4);
// the transcription API picks its decoder from the filename extension.
function audioFilename(type: string): string {
  if (type.includes('mp4')) return 'voice.mp4'
  if (type.includes('ogg')) return 'voice.ogg'
  return 'voice.webm'
}

/** POST one recorded voice turn; returns transcript, reply, and reply audio. */
export async function sendVoice(
  sessionId: string,
  recording: Blob,
): Promise<VoiceTurn> {
  const form = new FormData()
  form.append('session_id', sessionId)
  form.append('audio', recording, audioFilename(recording.type))
  const response = await fetch('/api/voice', { method: 'POST', body: form })
  if (!response.ok) throw new Error(`voice request failed: ${response.status}`)
  return response.json()
}

export async function fetchCrm(): Promise<CrmSnapshot> {
  const response = await fetch('/api/customers')
  if (!response.ok) throw new Error(`crm request failed: ${response.status}`)
  return response.json()
}

export async function fetchPolicy(): Promise<string> {
  const response = await fetch('/api/policy')
  if (!response.ok) throw new Error(`policy request failed: ${response.status}`)
  return response.text()
}

export async function resetDemo(): Promise<void> {
  await fetch('/api/reset', { method: 'POST' })
}

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

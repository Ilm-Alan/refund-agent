import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchCrm, fetchPolicy, resetDemo } from '../lib/api'
import type { AgentEvent, CrmSnapshot, Decision } from '../types'

const MAX_EVENTS = 800

// Event kinds that change CRM state, so the table refetches on them.
const CRM_MUTATIONS = new Set([
  'refund_processed',
  'refund_denied',
  'gate_refusal',
  'demo_reset',
])

const ALERT_KINDS = new Set(['error', 'retry', 'gate_refusal'])

export default function Admin() {
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [crm, setCrm] = useState<CrmSnapshot | null>(null)
  const [policy, setPolicy] = useState('')
  const ledger = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const refetchTimer = useRef<number>(undefined)

  const refetchCrm = useCallback(() => {
    window.clearTimeout(refetchTimer.current)
    refetchTimer.current = window.setTimeout(() => {
      fetchCrm().then(setCrm).catch(() => {})
    }, 250)
  }, [])

  useEffect(() => {
    fetchCrm().then(setCrm).catch(() => {})
    fetchPolicy().then(setPolicy).catch(() => {})
    setEvents([])
    const source = new EventSource('/api/events')
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.onmessage = (message) => {
      const event: AgentEvent = JSON.parse(message.data)
      setEvents((all) => [...all, event].slice(-MAX_EVENTS))
      if (CRM_MUTATIONS.has(event.kind)) refetchCrm()
    }
    return () => {
      source.close()
      window.clearTimeout(refetchTimer.current)
    }
  }, [refetchCrm])

  useEffect(() => {
    if (stick.current) {
      requestAnimationFrame(() => {
        ledger.current?.scrollTo({ top: ledger.current.scrollHeight })
      })
    }
  }, [events])

  function onLedgerScroll() {
    const el = ledger.current
    if (!el) return
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
  }

  return (
    <div className="admin-page">
      <section className="trace-pane">
        <div className="pane-head">
          <h2>Agent trace</h2>
          <span className={`live ${connected ? '' : 'off'}`}>
            {connected ? 'Live' : 'Reconnecting'}
          </span>
          <button
            type="button"
            className="ghost-btn"
            onClick={() => {
              void resetDemo().then(refetchCrm)
            }}
          >
            Reset demo data
          </button>
        </div>
        <div className="ledger" ref={ledger} onScroll={onLedgerScroll}>
          {events.length === 0 ? (
            <p className="ledger-empty">
              Waiting for activity. Events appear here as the agent works:
              model output, tool calls, policy verdicts, retries, and errors.
            </p>
          ) : (
            events.map((event) => <EventRow key={event.seq} event={event} />)
          )}
        </div>
      </section>
      <aside className="side-pane">
        <div className="side-section crm">
          <div className="pane-head">
            <h2>CRM</h2>
          </div>
          <div className="side-scroll">
            {crm && <CrmTable snapshot={crm} />}
            <div className="section-label">Decisions this run</div>
            {crm && crm.decisions.length > 0 ? (
              crm.decisions.map((decision, i) => (
                <DecisionRow key={i} decision={decision} />
              ))
            ) : (
              <p className="crm-email">None yet.</p>
            )}
          </div>
        </div>
        <div className="side-section policy">
          <div className="pane-head">
            <h2>Refund policy</h2>
          </div>
          <div className="side-scroll">
            <PolicyDoc text={policy} />
          </div>
        </div>
      </aside>
    </div>
  )
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour12: false })
}

function EventRow({ event }: { event: AgentEvent }) {
  const p = event.payload
  const alert = ALERT_KINDS.has(event.kind) || p.is_error === true
  return (
    <div className={`event ${alert ? 'alert' : ''}`}>
      <div className="seq">{event.seq}</div>
      <div>
        <div className="kind">{kindLabel(event)}</div>
        <div className="body">
          <EventBody event={event} />
        </div>
        <span className="session">{event.session_id}</span>
      </div>
      <div className="meta">
        {clock(event.at)}
        {typeof p.elapsed_ms === 'number' && (
          <>
            <br />
            {p.elapsed_ms.toLocaleString()} ms
          </>
        )}
      </div>
    </div>
  )
}

function kindLabel(event: AgentEvent): string {
  const p = event.payload
  const voice = p.channel === 'voice' ? ' (voice)' : ''
  switch (event.kind) {
    case 'customer_message':
      return `customer${voice}`
    case 'agent_reply':
      return `agent reply${voice}`
    case 'model_text':
      return 'model'
    case 'tool_call':
      return `tool call: ${p.tool}`
    case 'tool_result':
      return p.is_error ? `tool error: ${p.tool}` : `tool result: ${p.tool}`
    case 'policy_verdict':
      return 'policy verdict'
    case 'gate_refusal':
      return 'gate refusal'
    case 'refund_processed':
      return 'refund processed'
    case 'refund_denied':
      return 'denial recorded'
    case 'retry':
      return 'retry'
    case 'error':
      return 'error'
    case 'demo_reset':
      return 'demo reset'
    default:
      return event.kind
  }
}

function EventBody({ event }: { event: AgentEvent }) {
  const p = event.payload
  switch (event.kind) {
    case 'customer_message':
    case 'agent_reply':
    case 'model_text':
      return <>{String(p.text)}</>
    case 'tool_call':
      return <KeyValues data={p.input as Record<string, unknown>} />
    case 'tool_result': {
      const parsed = tryParse(String(p.result))
      if (p.is_error) {
        return <>{parsed && 'error' in parsed ? String(parsed.error) : String(p.result)}</>
      }
      return parsed ? (
        <Raw label="result" data={parsed} />
      ) : (
        <>{String(p.result)}</>
      )
    }
    case 'policy_verdict':
    case 'gate_refusal':
    case 'refund_denied':
      return <VerdictBody payload={p} />
    case 'refund_processed':
      return (
        <>
          <span className="stamp fill">refunded</span>
          <span className="amount">
            {typeof p.refund_amount === 'number'
              ? `$${p.refund_amount.toFixed(2)}`
              : ''}
          </span>{' '}
          {String(p.item_id)} on {String(p.order_id)}
        </>
      )
    case 'retry':
      return (
        <>
          {String(p.cause)}, attempt {String(p.attempt)} of{' '}
          {String(p.max_attempts)}, backing off {String(p.backoff_seconds)}s
        </>
      )
    case 'error':
      return (
        <>
          {String(p.where)}: {String(p.error)}
        </>
      )
    case 'demo_reset':
      return <>CRM restored to its on-disk state; sessions cleared.</>
    default:
      return <Raw label="payload" data={p} />
  }
}

function VerdictBody({ payload }: { payload: Record<string, unknown> }) {
  const ruleIds = (payload.rule_ids as string[]) ?? []
  const kind = String(payload.kind)
  const eligible = payload.eligible === true
  return (
    <>
      <span className={`stamp ${eligible ? 'fill' : 'red'}`}>
        {eligible ? kind : kind === 'escalate' ? 'escalate' : 'denied'}
      </span>
      {ruleIds.map((rule) => (
        <span key={rule} className={`stamp ${eligible ? '' : 'red'}`}>
          {rule}
        </span>
      ))}
      {typeof payload.refund_amount === 'number' && (
        <span className="amount">${payload.refund_amount.toFixed(2)}</span>
      )}
      <div className="verdict-summary">{String(payload.summary)}</div>
    </>
  )
}

function KeyValues({ data }: { data: Record<string, unknown> }) {
  return (
    <dl className="kv">
      {Object.entries(data).map(([key, value]) => (
        <div key={key} style={{ display: 'contents' }}>
          <dt>{key}</dt>
          <dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function Raw({ label, data }: { label: string; data: unknown }) {
  return (
    <details className="raw">
      <summary>{label}</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  )
}

function tryParse(text: string): Record<string, unknown> | null {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

function CrmTable({ snapshot }: { snapshot: CrmSnapshot }) {
  return (
    <table className="crm-table">
      <thead>
        <tr>
          <th>Customer</th>
          <th>Flags</th>
          <th className="num">Refunds/yr</th>
          <th className="num">Orders</th>
        </tr>
      </thead>
      <tbody>
        {snapshot.customers.map((customer) => (
          <tr key={customer.id}>
            <td>
              <div className="crm-name">{customer.name}</div>
              <div className="crm-email">{customer.email}</div>
            </td>
            <td>
              {customer.vip && <span className="stamp fill">VIP</span>}
              {customer.fraud_flag && <span className="stamp red">frozen</span>}
            </td>
            <td className="num">{customer.refunds_past_year}</td>
            <td className="num">{customer.orders.length}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DecisionRow({ decision }: { decision: Decision }) {
  const denied = decision.decision !== 'refund_processed'
  return (
    <div className="decision-row">
      <span className={`stamp ${denied ? 'red' : 'fill'}`}>
        {decision.decision.replace(/_/g, ' ')}
      </span>
      <span>
        {decision.item_id} on {decision.order_id}
        {typeof decision.refund_amount === 'number' && (
          <>
            {' '}
            <span className="amount">${decision.refund_amount.toFixed(2)}</span>
          </>
        )}
      </span>
      <span className="when">{clock(decision.at)}</span>
    </div>
  )
}

function PolicyDoc({ text }: { text: string }) {
  const blocks: React.ReactNode[] = []
  let list: string[] = []
  let paragraph: string[] = []
  let key = 0
  const flush = () => {
    if (list.length > 0) {
      blocks.push(
        <ul key={key++}>
          {list.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>,
      )
      list = []
    }
    if (paragraph.length > 0) {
      blocks.push(<p key={key++}>{paragraph.join(' ')}</p>)
      paragraph = []
    }
  }
  for (const line of text.split('\n')) {
    if (line.startsWith('- ')) {
      if (paragraph.length > 0) flush()
      list.push(line.slice(2))
    } else if (line.startsWith('  ') && list.length > 0) {
      // Continuation of a hard-wrapped list item.
      list[list.length - 1] += ' ' + line.trim()
    } else if (line.startsWith('## ')) {
      flush()
      blocks.push(<h4 key={key++}>{line.slice(3)}</h4>)
    } else if (line.startsWith('# ')) {
      flush()
      blocks.push(<h3 key={key++}>{line.slice(2)}</h3>)
    } else if (line.trim() === '') {
      flush()
    } else {
      if (list.length > 0) flush()
      paragraph.push(line.trim())
    }
  }
  flush()
  return <div className="policy-doc">{blocks}</div>
}

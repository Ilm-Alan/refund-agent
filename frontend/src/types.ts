export interface AgentEvent {
  seq: number
  kind: string
  session_id: string
  at: string
  payload: Record<string, unknown>
}

export interface ChatStreamEvent {
  kind: 'working' | 'reply' | 'done'
  label?: string
  text?: string
}

export interface OrderItem {
  id: string
  name: string
  category: string
  price: number
  final_sale: boolean
  refunded?: boolean
}

export interface Order {
  id: string
  date: string
  delivered: string | null
  status: string
  shipping: number
  items: OrderItem[]
}

export interface Customer {
  id: string
  name: string
  email: string
  joined: string
  vip: boolean
  fraud_flag: boolean
  refunds_past_year: number
  orders: Order[]
}

export interface Decision {
  decision: string
  at: string
  customer_id: string
  order_id: string
  item_id: string
  kind: string
  refund_amount: number | null
  rule_ids: string[]
  summary: string
}

export interface CrmSnapshot {
  customers: Customer[]
  decisions: Decision[]
}

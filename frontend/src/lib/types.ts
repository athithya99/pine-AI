export type Role = 'super_admin' | 'owner' | 'admin' | 'sales' | 'accounts'

export interface Tenant {
  id: string
  name: string
  logo_url: string | null
  gstin: string | null
  billing_address: string | null
  state: string | null
  contact_email: string | null
  contact_phone: string | null
  status: 'active' | 'suspended'
  timezone: string
  fy_start_month: number
  order_prefix: string
  quote_validity_hours: number
  ai_ordering_enabled: boolean
  ai_match_threshold: string
}

export interface User {
  id: string
  tenant_id: string | null
  name: string
  email: string | null
  mobile: string | null
  role: Role
  is_active: boolean
  last_login_at: string | null
}

export interface Me extends User { tenant: Tenant | null }

export interface Page<T> { items: T[]; total: number; page: number; size: number }

export type CustomerType = 'distributor' | 'retailer' | 'dealer' | 'other'

export interface Customer {
  id: string
  business_name: string
  contact_person: string | null
  whatsapp_number: string
  alternate_phone: string | null
  email: string | null
  gstin: string | null
  billing_address: string | null
  shipping_address: string | null
  city: string | null
  state: string | null
  pincode: string | null
  customer_type: CustomerType
  status: 'active' | 'lead' | 'inactive'
  tags: string[]
  price_list_id: string | null
  assigned_user_id: string | null
  credit_limit: string
  credit_days: number
  opening_balance: string
  whatsapp_opt_in: boolean
  opted_out: boolean
}

export type StockStatus = 'in_stock' | 'low' | 'out_of_stock'

export interface Product {
  id: string
  sku: string
  name: string
  brand: string | null
  category: string | null
  uom: string
  pack_size: string | null
  hsn_code: string | null
  gst_rate: string
  mrp: string | null
  base_price: string
  min_order_qty: string
  aliases: string[]
  stock_status: StockStatus
  is_active: boolean
}

export interface GstRate { id: string; rate: string; label: string; is_active: boolean }

export interface PriceList { id: string; name: string; description: string | null; is_active: boolean }

export interface PriceEntry { product_id: string; price: string }

export type SchemeType = 'percent' | 'flat_per_unit' | 'free_qty'

export interface Scheme {
  id: string
  name: string
  scheme_type: SchemeType
  value: string
  min_qty: string
  free_qty: string
  start_date: string
  end_date: string
  product_ids: string[]
  target: 'all' | 'tags' | 'customer_types'
  target_values: string[]
  is_active: boolean
}

export interface ImportResult {
  total_rows: number
  created: number
  updated: number
  failed: number
  errors: { row: number; errors: string[] }[]
}

export interface WaAccount {
  connected: boolean
  mode: 'simulator' | 'cloud' | null
  waba_id: string | null
  phone_number_id: string | null
  display_phone_number: string | null
  access_token_masked: string | null
  app_secret_masked: string | null
  webhook_url_path: string
  verify_token_hint: string | null
  simulator_enabled: boolean
}

export type TemplateStatus = 'DRAFT' | 'PENDING' | 'APPROVED' | 'REJECTED' | 'PAUSED' | 'DISABLED'

export interface WaTemplate {
  id: string
  name: string
  language: string
  category: 'MARKETING' | 'UTILITY' | 'AUTHENTICATION'
  header_text: string | null
  body: string
  footer: string | null
  buttons: { type: string; text: string }[] | null
  variable_count: number
  status: TemplateStatus
  rejection_reason: string | null
}

export interface Conversation {
  id: string
  customer_id: string
  customer_name: string
  whatsapp_number: string
  customer_status: string
  assigned_user_id: string | null
  opted_out: boolean
  whatsapp_opt_in: boolean
  bot_enabled: boolean
  unread_count: number
  last_message_at: string | null
  last_message_preview: string | null
  window_open: boolean
  window_closes_at: string | null
}

export type MsgStatus = 'received' | 'queued' | 'sent' | 'delivered' | 'read' | 'failed'

export interface ChatMessage {
  id: string
  direction: 'inbound' | 'outbound'
  msg_type: string
  text: string | null
  payload: Record<string, any> | null
  has_media: boolean
  media_mime: string | null
  media_filename: string | null
  status: MsgStatus
  error_code: string | null
  error_message: string | null
  sender: 'customer' | 'user' | 'bot' | 'system'
  sent_by_user_id: string | null
  created_at: string
}

export type OrderStatus =
  | 'draft' | 'quoted' | 'expired' | 'confirmed' | 'on_hold' | 'approved' | 'invoiced' | 'dispatched' | 'delivered' | 'cancelled'

export type OrderSource = 'panel' | 'whatsapp' | 'excel'

export type OrderAction =
  | 'edit' | 'send_quote' | 'confirm' | 'approve' | 'reject' | 'cancel' | 'invoice' | 'dispatch' | 'deliver'

export interface OrderSummary {
  id: string
  number: string
  customer_id: string
  customer_name: string
  sales_user_id: string | null
  sales_user_name: string | null
  source: OrderSource
  status: OrderStatus
  grand_total: string
  line_count: number
  hold_reasons: string | null
  quote_valid_until: string | null
  created_at: string
  updated_at: string
}

export interface OrderLine {
  id: string
  position: number
  product_id: string
  sku: string
  name: string
  uom: string
  hsn_code: string | null
  quantity: string
  free_qty: string
  unit_price: string
  price_source: string
  gross: string
  discount: string
  scheme_name: string | null
  taxable_value: string
  gst_rate: string
  gst_amount: string
  line_total: string
  out_of_stock: boolean
  match_confidence: string | null
  raw_text: string | null
  invoiced_qty: string | null
}

export interface OrderEvent {
  id: string
  kind: string
  from_status: OrderStatus | null
  to_status: OrderStatus | null
  actor: 'user' | 'buyer' | 'system'
  user_id: string | null
  user_name: string | null
  remark: string | null
  message_id: string | null
  notify_note: string | null
  created_at: string
}

export interface OrderDetail {
  id: string
  number: string
  customer_id: string
  customer_name: string
  customer_whatsapp: string
  sales_user_id: string | null
  sales_user_name: string | null
  created_by_name: string | null
  source: OrderSource
  status: OrderStatus
  revision: number
  notes: string | null
  gross_total: string
  discount_total: string
  taxable_total: string
  gst_total: string
  grand_total: string
  quoted_at: string | null
  quote_valid_until: string | null
  confirmed_at: string | null
  confirmed_by_buyer: boolean
  hold_reasons: string | null
  approved_at: string | null
  approval_remark: string | null
  invoice_number: string | null
  invoice_date: string | null
  vehicle_number: string | null
  transporter: string | null
  lr_number: string | null
  dispatched_at: string | null
  delivered_at: string | null
  cancelled_at: string | null
  cancel_reason: string | null
  cancelled_by_buyer: boolean
  created_at: string
  lines: OrderLine[]
  events: OrderEvent[]
  credit: { credit_limit: string; outstanding: string; available: string | null }
  allowed_actions: OrderAction[]
  ai_unclear: AiUnclear[] | null
  source_message: SourceMessage | null
}

export interface AiUnclear {
  text: string
  quantity: number | null
  unit: string | null
  reason: string
  candidates: { product_id: string; sku: string; name: string; score: number }[]
}

export interface SourceMessage {
  id: string
  msg_type: string
  text: string | null
  transcript: string | null
  has_media: boolean
  media_mime: string | null
  media_filename: string | null
  created_at: string
}

export interface AiStatus {
  provider: string
  is_mock: boolean
  configured: boolean
  model_fast: string
  model_smart: string
  reads_photos: boolean
  voice_notes: boolean
  ai_ordering_enabled: boolean
  ai_match_threshold: string
}

export interface AiLog {
  id: string
  created_at: string
  kind: string
  provider: string
  model: string
  intent: string | null
  input_text: string | null
  input_media: string | null
  output: Record<string, any> | null
  input_tokens: number
  output_tokens: number
  cost_usd: string | null
  latency_ms: number
  error: string | null
  message_id: string | null
  order_id: string | null
  order_number: string | null
  customer_name: string | null
}

export interface AiUsage {
  period_from: string
  period_to: string
  calls: number
  errors: number
  input_tokens: number
  output_tokens: number
  cost_usd: string
  avg_latency_ms: number
  orders_created: number
  by_intent: Record<string, number>
}

export const ORDER_STATUS_LABEL: Record<OrderStatus, string> = {
  draft: 'Draft', quoted: 'Quoted', expired: 'Expired', confirmed: 'Confirmed', on_hold: 'On hold',
  approved: 'Approved', invoiced: 'Invoiced', dispatched: 'Dispatched', delivered: 'Delivered', cancelled: 'Cancelled',
}

export const ORDER_STATUS_BADGE: Record<OrderStatus, string> = {
  draft: 'bg-slate-100 text-slate-600',
  quoted: 'bg-sky-50 text-sky-700',
  expired: 'bg-slate-100 text-slate-500',
  confirmed: 'bg-indigo-50 text-indigo-700',
  on_hold: 'bg-amber-50 text-amber-800',
  approved: 'bg-pine-50 text-pine-700',
  invoiced: 'bg-teal-50 text-teal-700',
  dispatched: 'bg-violet-50 text-violet-700',
  delivered: 'bg-emerald-50 text-emerald-700',
  cancelled: 'bg-red-50 text-red-700',
}

export const ROLE_LABEL: Record<Role, string> = {
  super_admin: 'Super admin', owner: 'Owner', admin: 'Admin', sales: 'Sales', accounts: 'Accounts',
}

export const isManager = (r?: Role) => r === 'owner' || r === 'admin'

export const inr = (v: string | number | null | undefined) =>
  v === null || v === undefined || v === '' ? '—'
    : Number(v).toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 })

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
  invoice_prefix: string
  credit_note_prefix: string
  invoice_auto_send: boolean
  reminders_enabled: boolean
  reminder_offsets: number[]
  receipt_prefix: string
  campaign_message_rate: string
  wa_daily_limit: number
  daily_summary_enabled: boolean
  daily_summary_time: string
  daily_summary_to: string | null
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
  header_format?: string | null
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
  handover_at?: string | null
  handover_reason?: string | null
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
  invoice_id: string | null
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

export type InvoiceStatus = 'review' | 'issued' | 'cancelled'
export type InvoiceSource = 'generated' | 'erp' | 'uploaded' | 'excel'

export interface InvoiceSummary {
  id: string
  number: string | null
  invoice_date: string | null
  due_date: string | null
  customer_id: string | null
  customer_name: string | null
  order_id: string | null
  order_number: string | null
  source: InvoiceSource
  status: InvoiceStatus
  total: string
  amount_paid: string
  balance_due: string
  overdue: boolean
  has_pdf: boolean
  pdf_name: string | null
  sent: boolean
  created_at: string
}

export interface InvoiceLine {
  id: string; position: number; description: string; sku: string | null; hsn_code: string | null; uom: string
  quantity: string; free_qty: string; rate: string; discount: string; taxable_value: string; gst_rate: string
  cgst: string; sgst: string; igst: string; total: string
}

export interface CreditNote {
  id: string; number: string; note_date: string; customer_id: string; customer_name: string | null
  invoice_id: string | null; invoice_number: string | null; amount: string; reason: string
  status: 'issued' | 'cancelled'; cancel_reason: string | null; created_at: string
}

export interface InvoiceDetail extends InvoiceSummary {
  seller_gstin: string | null
  buyer_gstin: string | null
  place_of_supply: string | null
  inter_state: boolean
  taxable_value: string
  cgst: string
  sgst: string
  igst: string
  tax_total: string
  round_off: string
  notes: string | null
  ai_extracted: Record<string, string | null> | null
  send_note: string | null
  sent_message_id: string | null
  cancel_reason: string | null
  cancelled_at: string | null
  confirmed_at: string | null
  confirmed_by_name: string | null
  created_by_name: string | null
  customer_whatsapp: string | null
  lines: InvoiceLine[]
  credit_notes: CreditNote[]
  amount_in_words: string
  reminders_paused: boolean
}

export interface LedgerRow {
  id: string; entry_date: string; kind: string; ref_id: string | null; ref_number: string | null
  narration: string | null; debit: string; credit: string; balance: string
}

export interface Statement {
  customer_id: string; customer_name: string; opening_balance_master: string; date_from: string; date_to: string
  opening: string; entries: LedgerRow[]; total_debit: string; total_credit: string; closing: string
}

export interface BalanceRow {
  customer_id: string; customer_name: string; whatsapp_number: string; opening_balance: string; balance: string
  overdue: string; credit_limit: string
}

export const isAccounts = (r?: Role) => r === 'owner' || r === 'admin' || r === 'accounts'

export type PaymentMode = 'link' | 'cash' | 'cheque' | 'neft' | 'rtgs' | 'upi' | 'other'
export type PaymentStatus = 'proposed' | 'pending_clearance' | 'confirmed' | 'bounced' | 'rejected'

export interface Payment {
  id: string; number: string; customer_id: string | null; customer_name: string | null; amount: string
  payment_date: string; mode: PaymentMode; status: PaymentStatus; source: 'gateway' | 'manual' | 'ai_proof'
  reference: string | null; cheque_bank: string | null; cheque_date: string | null; payer_name: string | null
  notes: string | null; unallocated: string; status_reason: string | null; receipt_sent: boolean
  receipt_note: string | null; message_id: string | null; has_proof_media: boolean
  ai_extracted: Record<string, any> | null; duplicate_of: string | null
  allocations: { invoice_id: string; invoice_number: string | null; amount: string }[]; created_at: string
}

export interface PaymentLinkT {
  id: string; customer_id: string; customer_name: string | null; amount: string; invoice_ids: string[] | null
  gateway: 'simulator' | 'razorpay'; short_url: string | null; status: 'created' | 'paid' | 'expired' | 'cancelled'
  purpose: string; expires_at: string | null; paid_at: string | null; created_at: string; send_note: string | null
}

export interface AgeingRow {
  customer_id: string; customer_name: string; whatsapp_number: string; invoices: number; not_due: string
  d0_30: string; d31_60: string; d61_90: string; d90_plus: string; total: string; overdue: string
  reminders_paused: boolean
}

export interface CollectionsDash {
  total_outstanding: string; overdue: string; collected_this_month: string; collected_via_link: string
  collected_offline: string; to_confirm: { count: number; amount: string }; cheques_pending: { count: number; amount: string }
}

export const PAYMENT_MODE_LABEL: Record<PaymentMode, string> = {
  link: 'Payment link', cash: 'Cash', cheque: 'Cheque', neft: 'NEFT', rtgs: 'RTGS', upi: 'UPI', other: 'Other',
}
export const PAYMENT_STATUS: Record<PaymentStatus, [string, string]> = {
  proposed: ['To confirm', 'bg-amber-50 text-amber-800'],
  pending_clearance: ['Cheque pending', 'bg-sky-50 text-sky-700'],
  confirmed: ['Received', 'bg-emerald-50 text-emerald-700'],
  bounced: ['Bounced', 'bg-red-50 text-red-700'],
  rejected: ['Rejected', 'bg-slate-100 text-slate-500'],
}
export const isPaymentsFull = (r?: Role) => r === 'owner' || r === 'accounts'
export const canCollect = (r?: Role) => r === 'owner' || r === 'admin' || r === 'accounts'

// ---------- campaigns (M9) ----------
export type CampaignStatus = 'draft' | 'scheduled' | 'sending' | 'completed' | 'cancelled'
export const CAMPAIGN_STATUS: Record<CampaignStatus, [string, string]> = {
  draft: ['Draft', 'bg-slate-100 text-slate-600'],
  scheduled: ['Scheduled', 'bg-sky-50 text-sky-700'],
  sending: ['Sending', 'bg-amber-50 text-amber-700'],
  completed: ['Sent', 'bg-emerald-50 text-emerald-700'],
  cancelled: ['Cancelled', 'bg-red-50 text-red-600'],
}
export interface Segment {
  customer_types?: CustomerType[]
  tags?: string[]
  cities?: string[]
  states?: string[]
  assigned_user_ids?: string[]
  no_order_days?: number | null
  ordered_within_days?: number | null
  min_outstanding?: string | null
  max_outstanding?: string | null
}
export interface CampaignVariable { kind: 'fixed' | 'field'; value: string }
export interface CampaignTemplateOpt {
  id: string; name: string; language: string; status: string; body: string; header_format: string | null
  header_text: string | null; footer: string | null; buttons: { type: string; text: string }[]; variable_count: number
}
export interface CampaignOptions {
  fields: { key: string; label: string }[]; tags: string[]; cities: string[]; states: string[]
  sales_users: { id: string; name: string }[]; templates: CampaignTemplateOpt[]; rate: string; daily_limit: number
}
export interface SegmentPreview {
  matched: number; eligible: number; opted_out: number; no_opt_in: number; rate: string; estimated_cost: string
  sample: string[]; capacity_now: number; daily_limit: number
}
export interface CampaignStats {
  targeted: number; excluded: number; excluded_reasons: { reason: string; count: number }[]; waiting: number
  sent: number; delivered: number; read: number; failed: number; failed_reasons: { reason: string; count: number }[]
  replied: number; order_taps: number; orders: number; order_value: string; ordering_buyers: number
  messages_charged: number; estimated_cost: string; attribution_days: number
}
export interface Campaign {
  id: string; name: string; status: CampaignStatus; template_id: string | null; template_name: string | null
  template_body: string | null; template_header_format: string | null; template_buttons: { text: string }[] | null
  variables: CampaignVariable[]; segment: Segment; has_media: boolean; media_filename: string | null
  media_mime: string | null; order_button_index: number | null; products: { id: string; sku: string; name: string }[]
  scheduled_at: string | null; started_at: string | null; completed_at: string | null; cancelled_at: string | null
  waiting_note: string | null; last_test_at: string | null; last_test_to: string | null
  created_by_name: string | null; sent_by_name: string | null; created_at: string; stats: CampaignStats | null
}
export interface CampaignRowT {
  id: string; name: string; status: CampaignStatus; template_name: string | null; scheduled_at: string | null
  started_at: string | null; created_at: string; targeted: number; sent: number; read: number; orders: number
  order_value: string
}
export interface RecipientRowT {
  id: string; customer_id: string; customer_name: string; whatsapp_number: string; status: string; delivery: string
  reason: string | null; queued_at: string | null; replied: boolean; order_tapped: boolean; orders: number
  order_value: string
}

// ---------- assistant & dashboard (M10) ----------
export interface AnswerColumn { key: string; label: string; type: 'text' | 'money' | 'int' | 'number' | 'date' | 'datetime' }
export interface AssistantMsg {
  id: string; created_at: string; question: string; kind: 'answer' | 'rule_proposal' | 'unknown' | 'error'
  tool: string | null; params: Record<string, any> | null; answer: string; columns: AnswerColumn[] | null
  rows: Record<string, any>[] | null; total_rows: number; rule_id: string | null; provider: string | null
}
export interface AutomationRuleT {
  id: string; kind: string; description: string; params: Record<string, any>; weekdays: number[]; hour: number
  is_active: boolean; last_run_on: string | null; last_run_summary: string | null; created_at: string
}
export interface DailySummaryT {
  id: string; summary_date: string; data: Record<string, any>; text: string; sent_to: string | null
  sent_at: string | null; send_error: string | null; created_at: string
}
export interface Faq {
  id: string; question: string; answer: string; answer_ta: string | null; answer_hi: string | null
  keywords: string[]; is_active: boolean; times_used: number; created_at: string
}
export interface DashboardData {
  period: { start: string; end: string; label: string }
  scope: string
  orders: { count: number; value: string; average: string; by_source: { source: string; orders: number; value: string }[] }
  collections: { amount: string; count: number; by_mode: { mode: string; payments: number; amount: string }[] }
  outstanding: { total: string; overdue: string; overdue_customers: number }
  top_customers: { customer_id: string; customer: string; order_value: string; orders: number }[]
  top_products: { product: string; quantity: string; uom: string; value: string }[]
  ai: { whatsapp_orders: number; lines_matched: number; items_asked: number; match_rate: number | null }
  series: { date: string; orders: number; order_value: string; collections: string }[]
}

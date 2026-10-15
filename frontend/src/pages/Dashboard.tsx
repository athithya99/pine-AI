import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/ui'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import type { Page, Product, Scheme } from '../lib/types'

export function Dashboard() {
  const { me } = useAuth()
  const [stats, setStats] = useState({ customers: 0, products: 0, lowStock: 0, schemes: 0 })
  const [orders, setOrders] = useState<Record<string, number>>({})

  useEffect(() => {
    Promise.all([
      api.get<Page<unknown>>('/customers', { params: { size: 1 } }),
      api.get<Page<Product>>('/products', { params: { size: 1 } }),
      api.get<Page<Product>>('/products', { params: { size: 1, stock_status: 'low' } }),
      api.get<Page<Product>>('/products', { params: { size: 1, stock_status: 'out_of_stock' } }),
      api.get<Scheme[]>('/schemes', { params: { current: true } }),
    ]).then(([c, p, low, oos, s]) =>
      setStats({ customers: c.data.total, products: p.data.total, lowStock: low.data.total + oos.data.total, schemes: s.data.length }),
    ).catch(() => {})
    api.get<Record<string, number>>('/orders/counts').then((r) => setOrders(r.data)).catch(() => {})
  }, [])

  const n = (...keys: string[]) => keys.reduce((a, k) => a + (orders[k] ?? 0), 0)
  const orderCards = [
    { label: 'Orders needing approval', value: n('on_hold'), to: '/orders?tab=approval', warn: n('on_hold') > 0 },
    { label: 'Quotes waiting for buyer', value: n('quoted'), to: '/orders?tab=open', warn: false },
    { label: 'Approved, to invoice', value: n('approved'), to: '/orders?tab=to_invoice', warn: false },
    { label: 'To dispatch / in transit', value: n('invoiced', 'dispatched'), to: '/orders?tab=to_ship', warn: false },
  ]

  const cards = [
    { label: 'Customers', value: stats.customers, to: '/customers' },
    { label: 'Active products', value: stats.products, to: '/products' },
    { label: 'Low or out of stock', value: stats.lowStock, to: '/products' },
    { label: 'Schemes running today', value: stats.schemes, to: '/schemes' },
  ]

  return (
    <>
      <PageHeader title={`Welcome, ${me?.name?.split(' ')[0] ?? ''}`} subtitle={me?.tenant?.name} />
      <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {orderCards.map((c) => (
          <Link key={c.label} to={c.to} className={`card p-5 hover:border-pine-500 ${c.warn ? 'border-amber-300 bg-amber-50' : ''}`}>
            <div className={`text-sm ${c.warn ? 'text-amber-800' : 'text-slate-500'}`}>{c.label}</div>
            <div className="mt-1 text-3xl font-semibold text-slate-900">{c.value}</div>
          </Link>
        ))}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map((c) => (
          <Link key={c.label} to={c.to} className="card p-5 hover:border-pine-500">
            <div className="text-sm text-slate-500">{c.label}</div>
            <div className="mt-1 text-3xl font-semibold text-slate-900">{c.value}</div>
          </Link>
        ))}
      </div>
      <div className="card mt-6 p-5 text-sm text-slate-600">
        Invoices, collections and the AI assistant will appear here as those modules go live.
      </div>
    </>
  )
}

import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Layout, RequireManager } from './components/Layout'
import { ToastProvider } from './components/ui'
import { AuthProvider, useAuth } from './lib/auth'
import { AiUsage } from './pages/AiUsage'
import { Customers } from './pages/Customers'
import { Dashboard } from './pages/Dashboard'
import { Inbox } from './pages/Inbox'
import { Login } from './pages/Login'
import { OrderDetail } from './pages/OrderDetail'
import { OrderForm } from './pages/OrderForm'
import { Orders } from './pages/Orders'
import { PriceLists } from './pages/PriceLists'
import { Products } from './pages/Products'
import { Schemes } from './pages/Schemes'
import { Settings } from './pages/Settings'
import { Users } from './pages/Users'
import { WhatsAppSettings } from './pages/WhatsAppSettings'

function Protected() {
  const { me, loading } = useAuth()
  if (loading) return <div className="grid min-h-screen place-items-center text-sm text-slate-500">Loading…</div>
  if (!me) return <Navigate to="/login" replace />
  if (!me.tenant_id) return <div className="p-8 text-sm text-slate-600">Platform admin console is not built yet. Use the API docs at /api/docs.</div>
  return <Layout />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<Protected />}>
              <Route index element={<Dashboard />} />
              <Route path="inbox" element={<Inbox />} />
              <Route path="orders" element={<Orders />} />
              <Route path="orders/new" element={<OrderForm />} />
              <Route path="orders/:id" element={<OrderDetail />} />
              <Route path="orders/:id/edit" element={<OrderForm />} />
              <Route path="customers" element={<Customers />} />
              <Route path="products" element={<Products />} />
              <Route path="price-lists" element={<PriceLists />} />
              <Route path="schemes" element={<Schemes />} />
              <Route path="users" element={<RequireManager><Users /></RequireManager>} />
              <Route path="settings" element={<Settings />} />
              <Route path="ai-usage" element={<RequireManager><AiUsage /></RequireManager>} />
              <Route path="whatsapp" element={<RequireManager><WhatsAppSettings /></RequireManager>} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}

import { useState, useEffect } from 'react'
import { Bell, Plus, Trash2, CheckCircle, AlertTriangle, XCircle, RefreshCw } from 'lucide-react'
import api, { alertsApi } from '../services/api'

function Alerts() {
    const [feed, setFeed] = useState([])
    const [loading, setLoading] = useState(true)
    const [refreshing, setRefreshing] = useState(false)
    const [showModal, setShowModal] = useState(false)
    const [form, setForm] = useState({
        symbol: '',
        alert_type: 'PRICE',
        condition: 'ABOVE',
        threshold: 0
    })

    useEffect(() => {
        loadData()
    }, [])

    const loadData = async () => {
        setLoading(true)
        try {
            const res = await api.get('/alerts/feed')
            setFeed(res.data.feed || [])
        } catch (error) {
            console.error('Error loading feed:', error)
        }
        setLoading(false)
    }

    const handleRefresh = async () => {
        setRefreshing(true)
        try {
            await api.post('/alerts/fetch-feed')
            await loadData()
        } catch (error) {
            console.error('Error refreshing feed:', error)
        }
        setRefreshing(false)
    }

    const createAlert = async () => {
        try {
            await alertsApi.create(form)
            setShowModal(false)
            setForm({ symbol: '', alert_type: 'PRICE', condition: 'ABOVE', threshold: 0 })
            // trigger fetch to maybe get news for new symbol
            handleRefresh()
        } catch (error) {
            console.error('Error creating alert:', error)
        }
    }

    const getSourceIcon = (type, source) => {
        if (type === 'ALERT') return <AlertTriangle className="w-5 h-5 text-primary-400" />
        return <span className="text-xl">📰</span>
    }

    return (
        <div className="space-y-6 animate-fade-in">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white">Market Pulse</h1>
                    <p className="text-dark-400">Live Alerts, News & Social Sentiment</p>
                </div>
                <div className="flex gap-2">
                    <button onClick={handleRefresh} className="btn-secondary flex items-center gap-2" disabled={refreshing}>
                        <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
                        Refresh Feed
                    </button>
                    <button onClick={() => setShowModal(true)} className="btn-primary flex items-center gap-2">
                        <Plus className="w-4 h-4" />
                        Create Alert
                    </button>
                </div>
            </div>

            {/* Feed */}
            <div className="space-y-4">
                {feed.length === 0 ? (
                    <div className="glass-card p-12 text-center">
                        <p className="text-dark-400">No recent updates. Click Refresh to fetch latest news.</p>
                    </div>
                ) : (
                    feed.map((item, index) => (
                        <div key={index} className="glass-card p-4 hover:bg-dark-700/50 transition-colors">
                            <div className="flex gap-4">
                                <div className="flex-shrink-0 mt-1">
                                    {getSourceIcon(item.type, item.source || '')}
                                </div>
                                <div className="flex-1">
                                    <div className="flex items-start justify-between mb-1">
                                        <h3 className="font-semibold text-white text-lg leading-snug">
                                            {item.type === 'ALERT' ? (
                                                <span className="text-primary-400">
                                                    Price Alert: {item.symbol} {item.condition} {item.threshold}
                                                </span>
                                            ) : (
                                                <a href={item.url} target="_blank" rel="noopener noreferrer" className="hover:text-primary-400 hover:underline">
                                                    {item.title}
                                                </a>
                                            )}
                                        </h3>
                                        <span className="text-xs text-dark-400 whitespace-nowrap ml-2">
                                            {new Date(item.published_at).toLocaleString()}
                                        </span>
                                    </div>

                                    {item.type !== 'ALERT' && (
                                        <p className="text-dark-300 text-sm mb-2 line-clamp-2">
                                            {item.summary}
                                        </p>
                                    )}

                                    <div className="flex items-center gap-3 text-xs text-dark-400">
                                        <span className="px-2 py-1 bg-dark-800 rounded uppercase font-bold tracking-wider">
                                            {item.source}
                                        </span>
                                        {item.related_symbols && (
                                            <span className="text-primary-400 font-mono">
                                                {item.related_symbols.split(',').map(s => `#${s.trim()}`).join(' ')}
                                            </span>
                                        )}
                                        {item.triggered_at && (
                                            <span className="text-danger-400 flex items-center gap-1">
                                                <AlertTriangle className="w-3 h-3" />
                                                Triggered
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </div>
                    ))
                )}
            </div>


            {/* Create Alert Modal */}
            {showModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="glass-card p-6 w-full max-w-md">
                        <h2 className="text-xl font-bold text-white mb-4">Create Alert</h2>
                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm text-dark-400 mb-1">Symbol</label>
                                <input
                                    type="text"
                                    className="input-field"
                                    value={form.symbol}
                                    onChange={(e) => setForm({ ...form, symbol: e.target.value.toUpperCase() })}
                                    placeholder="e.g., TCS"
                                />
                            </div>

                            <div>
                                <label className="block text-sm text-dark-400 mb-1">Alert Type</label>
                                <select
                                    className="input-field"
                                    value={form.alert_type}
                                    onChange={(e) => setForm({ ...form, alert_type: e.target.value })}
                                >
                                    <option value="PRICE">Price</option>
                                    <option value="RSI">RSI</option>
                                </select>
                            </div>

                            <div>
                                <label className="block text-sm text-dark-400 mb-1">Condition</label>
                                <select
                                    className="input-field"
                                    value={form.condition}
                                    onChange={(e) => setForm({ ...form, condition: e.target.value })}
                                >
                                    <option value="ABOVE">Goes Above</option>
                                    <option value="BELOW">Goes Below</option>
                                </select>
                            </div>

                            <div>
                                <label className="block text-sm text-dark-400 mb-1">
                                    {form.alert_type === 'PRICE' ? 'Price (₹)' : 'RSI Value'}
                                </label>
                                <input
                                    type="number"
                                    className="input-field"
                                    value={form.threshold}
                                    onChange={(e) => setForm({ ...form, threshold: parseFloat(e.target.value) })}
                                />
                            </div>

                            <div className="flex gap-3 pt-4">
                                <button onClick={() => setShowModal(false)} className="flex-1 btn-secondary">
                                    Cancel
                                </button>
                                <button onClick={createAlert} className="flex-1 btn-primary">
                                    Create Alert
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

export default Alerts

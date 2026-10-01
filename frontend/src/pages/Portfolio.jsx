import { useState, useEffect } from 'react'
import {
    Wallet, TrendingUp, TrendingDown, Plus, Edit, Trash2,
    ArrowUpRight, ArrowDownRight, RefreshCw
} from 'lucide-react'
import { portfolioApi } from '../services/api'

function Portfolio() {
    const [holdings, setHoldings] = useState([])
    const [performance, setPerformance] = useState(null)
    const [loading, setLoading] = useState(true)
    const [filter, setFilter] = useState('all')
    const [showAddModal, setShowAddModal] = useState(false)

    useEffect(() => {
        loadPortfolio()
    }, [])

    const loadPortfolio = async () => {
        setLoading(true)
        try {
            const [holdingsRes, perfRes] = await Promise.all([
                portfolioApi.getAll(),
                portfolioApi.getPerformance()
            ])
            setHoldings(holdingsRes.data.holdings || [])
            setPerformance(perfRes.data)
        } catch (error) {
            console.error('Error loading portfolio:', error)
        }
        setLoading(false)
    }

    const filteredHoldings = holdings.filter(h => {
        if (filter === 'all') return true
        return h.investment_strategy === filter
    })

    return (
        <div className="space-y-6 animate-fade-in">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white">Portfolio</h1>
                    <p className="text-dark-400">Track your investments</p>
                </div>
                <div className="flex items-center gap-3">
                    <button onClick={loadPortfolio} className="btn-secondary">
                        <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    </button>
                    <button onClick={() => setShowAddModal(true)} className="btn-primary flex items-center gap-2">
                        <Plus className="w-4 h-4" />
                        Add Stock
                    </button>
                </div>
            </div>

            {/* Performance Cards */}
            {performance && (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div className="stat-card">
                        <p className="text-sm text-dark-400">Total Investment</p>
                        <p className="text-2xl font-bold text-white">₹{performance.total_investment?.toLocaleString()}</p>
                    </div>
                    <div className="stat-card">
                        <p className="text-sm text-dark-400">Current Value</p>
                        <p className="text-2xl font-bold text-white">₹{performance.total_current_value?.toLocaleString()}</p>
                    </div>
                    <div className="stat-card">
                        <p className="text-sm text-dark-400">Total P&L</p>
                        <div className={`flex items-center gap-2 ${performance.total_pnl >= 0 ? 'text-success-400' : 'text-danger-400'}`}>
                            {performance.total_pnl >= 0 ? <ArrowUpRight className="w-5 h-5" /> : <ArrowDownRight className="w-5 h-5" />}
                            <p className="text-2xl font-bold">₹{Math.abs(performance.total_pnl)?.toLocaleString()}</p>
                        </div>
                        <p className={`text-sm ${performance.total_pnl_percent >= 0 ? 'text-success-400' : 'text-danger-400'}`}>
                            {performance.total_pnl_percent >= 0 ? '+' : ''}{performance.total_pnl_percent}%
                        </p>
                    </div>
                    <div className="stat-card">
                        <p className="text-sm text-dark-400">Allocation</p>
                        <div className="mt-2">
                            <div className="flex justify-between text-sm mb-1">
                                <span className="text-primary-400">Short-term</span>
                                <span className="text-white">{performance.allocation?.short_term_percent}%</span>
                            </div>
                            <div className="flex justify-between text-sm">
                                <span className="text-purple-400">Long-term</span>
                                <span className="text-white">{performance.allocation?.long_term_percent}%</span>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* Filter Tabs */}
            <div className="flex gap-2">
                {[
                    { id: 'all', label: 'All Holdings' },
                    { id: 'short-term', label: 'Short-term' },
                    { id: 'long-term', label: 'Long-term' }
                ].map((tab) => (
                    <button
                        key={tab.id}
                        onClick={() => setFilter(tab.id)}
                        className={`px-4 py-2 font-medium rounded-lg transition-colors ${filter === tab.id
                                ? 'bg-primary-600 text-white'
                                : 'bg-dark-700 text-dark-400 hover:text-white'
                            }`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Holdings Table */}
            <div className="glass-card overflow-hidden">
                {loading ? (
                    <div className="flex items-center justify-center py-12">
                        <RefreshCw className="w-8 h-8 animate-spin text-primary-500" />
                    </div>
                ) : filteredHoldings.length === 0 ? (
                    <div className="text-center py-12">
                        <Wallet className="w-12 h-12 text-dark-600 mx-auto mb-3" />
                        <p className="text-dark-400">No holdings yet</p>
                        <button onClick={() => setShowAddModal(true)} className="btn-primary mt-4">
                            Add Your First Stock
                        </button>
                    </div>
                ) : (
                    <table className="w-full">
                        <thead className="bg-dark-700/50">
                            <tr>
                                <th className="text-left p-4 text-dark-400 font-medium">Stock</th>
                                <th className="text-left p-4 text-dark-400 font-medium">Strategy</th>
                                <th className="text-right p-4 text-dark-400 font-medium">Qty</th>
                                <th className="text-right p-4 text-dark-400 font-medium">Avg Buy</th>
                                <th className="text-right p-4 text-dark-400 font-medium">Current</th>
                                <th className="text-right p-4 text-dark-400 font-medium">P&L</th>
                                <th className="text-right p-4 text-dark-400 font-medium">Days</th>
                                <th className="text-right p-4 text-dark-400 font-medium">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {filteredHoldings.map((holding) => (
                                <tr key={holding.id} className="border-t border-dark-700 hover:bg-dark-700/30">
                                    <td className="p-4">
                                        <p className="font-semibold text-white">{holding.symbol}</p>
                                    </td>
                                    <td className="p-4">
                                        <span className={`badge ${holding.investment_strategy === 'short-term' ? 'badge-info' : 'badge-warning'
                                            }`}>
                                            {holding.investment_strategy}
                                        </span>
                                    </td>
                                    <td className="p-4 text-right text-white">{holding.quantity}</td>
                                    <td className="p-4 text-right text-white">₹{holding.avg_buy_price?.toFixed(2)}</td>
                                    <td className="p-4 text-right text-white">₹{holding.current_price?.toFixed(2)}</td>
                                    <td className="p-4 text-right">
                                        <p className={holding.pnl >= 0 ? 'text-success-400' : 'text-danger-400'}>
                                            ₹{holding.pnl?.toFixed(2)}
                                        </p>
                                        <p className={`text-sm ${holding.pnl_percent >= 0 ? 'text-success-400' : 'text-danger-400'}`}>
                                            {holding.pnl_percent >= 0 ? '+' : ''}{holding.pnl_percent}%
                                        </p>
                                    </td>
                                    <td className="p-4 text-right text-dark-400">{holding.holding_days}</td>
                                    <td className="p-4 text-right">
                                        <div className="flex items-center justify-end gap-2">
                                            <button className="p-2 text-dark-400 hover:text-white hover:bg-dark-600 rounded-lg">
                                                <Edit className="w-4 h-4" />
                                            </button>
                                            <button className="p-2 text-dark-400 hover:text-danger-400 hover:bg-dark-600 rounded-lg">
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    )
}

export default Portfolio

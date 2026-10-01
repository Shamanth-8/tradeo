import { useState, useEffect, lazy, Suspense } from 'react'
import {
    Target, TrendingUp, TrendingDown, Plus, RefreshCw,
    ArrowUpRight, ArrowDownRight, RotateCcw
} from 'lucide-react'
import { paperTradingApi, stockApi } from '../services/api'
import FlyBrainPanel from '../components/FlyBrainPanel'
import AutomatedPaperAccount from '../components/AutomatedPaperAccount'

// Lazy: it pulls in recharts, which the rest of this page doesn't need.
const FlyHistoryLab = lazy(() => import('../components/FlyHistoryLab'))
const FlyTestLab = lazy(() => import('../components/FlyTestLab'))

function PaperTrading() {
    const [account, setAccount] = useState(null)
    const [portfolio, setPortfolio] = useState([])
    const [history, setHistory] = useState([])
    const [loading, setLoading] = useState(true)
    const [showTradeModal, setShowTradeModal] = useState(false)
    const [tradeType, setTradeType] = useState('buy')
    const [tradeForm, setTradeForm] = useState({ symbol: '', quantity: 1, strategy: 'short-term' })

    useEffect(() => {
        loadData()
    }, [])

    const loadData = async () => {
        setLoading(true)
        try {
            const [accountRes, portfolioRes, historyRes] = await Promise.all([
                paperTradingApi.getAccount(),
                paperTradingApi.getPortfolio(),
                paperTradingApi.getHistory()
            ])
            setAccount(accountRes.data)
            setPortfolio(portfolioRes.data.holdings || [])
            setHistory(historyRes.data.trades || [])
        } catch (error) {
            console.error('Error loading data:', error)
        }
        setLoading(false)
    }

    const handleTrade = async () => {
        try {
            if (tradeType === 'buy') {
                await paperTradingApi.buy(tradeForm)
            } else {
                await paperTradingApi.sell(tradeForm)
            }
            setShowTradeModal(false)
            loadData()
        } catch (error) {
            console.error('Trade error:', error)
        }
    }

    const handleReset = async () => {
        if (confirm('Reset paper trading account? This will clear all trades.')) {
            try {
                await paperTradingApi.reset()
                loadData()
            } catch (error) {
                console.error('Reset error:', error)
            }
        }
    }

    return (
        <div className="space-y-6 animate-fade-in">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white">Paper Trading</h1>
                    <p className="text-dark-400">The automated account the app&apos;s agents trade, then your own manual practice account further down</p>
                </div>
                <div className="flex items-center gap-3">
                    <button onClick={handleReset} className="btn-secondary flex items-center gap-2">
                        <RotateCcw className="w-4 h-4" />
                        Reset
                    </button>
                    <button onClick={() => { setTradeType('buy'); setShowTradeModal(true) }} className="btn-success flex items-center gap-2">
                        <Plus className="w-4 h-4" />
                        Buy
                    </button>
                    <button onClick={() => { setTradeType('sell'); setShowTradeModal(true) }} className="btn-danger flex items-center gap-2">
                        Sell
                    </button>
                </div>
            </div>

            {/* The automated side: its trades live on the autopilot's paper
                account, separate from the manual Buy/Sell account below. */}
            {/* The whole automated account first: every agent's trades. The
                fly brain below is one of those agents. */}
            <AutomatedPaperAccount />

            <FlyBrainPanel />

            <Suspense fallback={<div className="glass-card p-6 text-sm text-dark-400">Loading history lab…</div>}>
                <FlyHistoryLab />
            </Suspense>

            <Suspense fallback={<div className="glass-card p-6 text-sm text-dark-400">Loading test lab…</div>}>
                <FlyTestLab />
            </Suspense>

            <div>
                <h2 className="text-lg font-semibold text-white">Manual practice account</h2>
                <p className="text-sm text-dark-400">Your own Buy/Sell trades — separate from the fly brain's account above</p>
            </div>

            {/* Account Summary */}
            {account && (
                <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                    <div className="stat-card">
                        <p className="text-sm text-dark-400">Starting Capital</p>
                        <p className="text-xl font-bold text-white">₹{account.starting_capital?.toLocaleString()}</p>
                    </div>
                    <div className="stat-card">
                        <p className="text-sm text-dark-400">Available Cash</p>
                        <p className="text-xl font-bold text-white">₹{account.current_cash?.toLocaleString()}</p>
                    </div>
                    <div className="stat-card">
                        <p className="text-sm text-dark-400">Holdings Value</p>
                        <p className="text-xl font-bold text-white">₹{account.holdings_value?.toLocaleString()}</p>
                    </div>
                    <div className="stat-card">
                        <p className="text-sm text-dark-400">Total Value</p>
                        <p className="text-xl font-bold text-primary-400">₹{account.total_value?.toLocaleString()}</p>
                    </div>
                    <div className="stat-card">
                        <p className="text-sm text-dark-400">Total P&L</p>
                        <div className={`flex items-center gap-1 ${account.total_pnl >= 0 ? 'text-success-400' : 'text-danger-400'}`}>
                            {account.total_pnl >= 0 ? <ArrowUpRight className="w-5 h-5" /> : <ArrowDownRight className="w-5 h-5" />}
                            <p className="text-xl font-bold">₹{Math.abs(account.total_pnl)?.toLocaleString()}</p>
                        </div>
                    </div>
                </div>
            )}

            {/* Stats Row */}
            {account && (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div className="glass-card p-4 text-center">
                        <p className="text-3xl font-bold text-white">{account.total_trades}</p>
                        <p className="text-sm text-dark-400">Total Trades</p>
                    </div>
                    <div className="glass-card p-4 text-center">
                        <p className="text-3xl font-bold text-success-400">{account.winning_trades}</p>
                        <p className="text-sm text-dark-400">Winning</p>
                    </div>
                    <div className="glass-card p-4 text-center">
                        <p className="text-3xl font-bold text-danger-400">{account.losing_trades}</p>
                        <p className="text-sm text-dark-400">Losing</p>
                    </div>
                    <div className="glass-card p-4 text-center">
                        <p className="text-3xl font-bold text-primary-400">
                            {account.total_trades > 0 ? ((account.winning_trades / account.total_trades) * 100).toFixed(1) : 0}%
                        </p>
                        <p className="text-sm text-dark-400">Win Rate</p>
                    </div>
                </div>
            )}

            {/* Holdings */}
            <div className="glass-card p-6">
                <h3 className="text-lg font-semibold text-white mb-4">Current Holdings</h3>
                {portfolio.length === 0 ? (
                    <p className="text-dark-400 text-center py-8">No holdings yet</p>
                ) : (
                    <table className="w-full">
                        <thead>
                            <tr className="border-b border-dark-700">
                                <th className="text-left p-3 text-dark-400">Stock</th>
                                <th className="text-left p-3 text-dark-400">Strategy</th>
                                <th className="text-right p-3 text-dark-400">Qty</th>
                                <th className="text-right p-3 text-dark-400">Avg Price</th>
                                <th className="text-right p-3 text-dark-400">Current</th>
                                <th className="text-right p-3 text-dark-400">P&L</th>
                            </tr>
                        </thead>
                        <tbody>
                            {portfolio.map((h) => (
                                <tr key={h.symbol} className="border-b border-dark-700/50">
                                    <td className="p-3 font-semibold text-white">{h.symbol}</td>
                                    <td className="p-3">
                                        <span className={`badge ${h.strategy === 'short-term' ? 'badge-info' : 'badge-warning'}`}>
                                            {h.strategy}
                                        </span>
                                    </td>
                                    <td className="p-3 text-right text-white">{h.quantity}</td>
                                    <td className="p-3 text-right text-white">₹{h.avg_price?.toFixed(2)}</td>
                                    <td className="p-3 text-right text-white">₹{h.current_price?.toFixed(2)}</td>
                                    <td className={`p-3 text-right ${h.pnl >= 0 ? 'text-success-400' : 'text-danger-400'}`}>
                                        ₹{h.pnl?.toFixed(2)} ({h.pnl_percent}%)
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* Recent Trades */}
            <div className="glass-card p-6">
                <h3 className="text-lg font-semibold text-white mb-4">Recent Trades</h3>
                {history.length === 0 ? (
                    <p className="text-dark-400 text-center py-8">No trades yet</p>
                ) : (
                    <div className="space-y-2">
                        {history.slice(0, 10).map((trade) => (
                            <div key={trade.id} className="flex items-center justify-between p-3 bg-dark-700/30 rounded-lg">
                                <div className="flex items-center gap-4">
                                    <span className={`px-2 py-1 rounded text-xs font-bold ${trade.trade_type === 'BUY' ? 'bg-success-500/20 text-success-400' : 'bg-danger-500/20 text-danger-400'
                                        }`}>
                                        {trade.trade_type}
                                    </span>
                                    <span className="font-semibold text-white">{trade.symbol}</span>
                                    <span className="text-dark-400">{trade.quantity} shares @ ₹{trade.price}</span>
                                </div>
                                <div className="text-right">
                                    <p className="text-dark-400 text-sm">{new Date(trade.executed_at).toLocaleDateString()}</p>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Trade Modal */}
            {showTradeModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 animate-fade-in">
                    <div className="glass-card p-6 w-full max-w-md">
                        <h2 className="text-xl font-bold text-white mb-4">
                            {tradeType === 'buy' ? 'Buy Stock' : 'Sell Stock'}
                        </h2>

                        {/* Quote Section */}
                        <div className="mb-4 p-3 bg-dark-700/30 rounded-lg">
                            <div className="flex gap-2 mb-2">
                                <input
                                    type="text"
                                    className="input-field flex-1"
                                    value={tradeForm.symbol}
                                    onChange={(e) => setTradeForm({ ...tradeForm, symbol: e.target.value.toUpperCase() })}
                                    placeholder="Symbol (e.g. TCS)"
                                />
                                <button
                                    onClick={async () => {
                                        if (!tradeForm.symbol) return;
                                        setLoading(true);
                                        try {
                                            // Quick hack to get price via stock detail API
                                            // In a real app, we'd have a specific quote endpoint or use the stockApi.getDetail 
                                            // but since we don't have a direct 'getQuote' in api.js yet, let's assume we can fetch it.
                                            // Actually, let's use the valid stockApi.getDetails if it exists or fallback.
                                            // A better way for now is just rely on the user or implement a helper. 
                                            // Let's implement a simple fetch here.
                                            const res = await stockApi.getDetails(tradeForm.symbol + (tradeForm.symbol.endsWith('.NS') ? '' : '.NS'));
                                            if (res.data && !res.data.error) {
                                                setTradeForm(prev => ({
                                                    ...prev,
                                                    originalPrice: res.data.current_price,
                                                    estimatedPrice: res.data.current_price
                                                }));
                                            } else {
                                                alert("Could not fetch quote. Please check symbol.");
                                            }
                                        } catch (e) {
                                            console.error(e);
                                            alert("Failed to fetch quote");
                                        }
                                        setLoading(false);
                                    }}
                                    className="btn-secondary whitespace-nowrap"
                                    disabled={loading}
                                >
                                    {loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : "Get Quote"}
                                </button>
                            </div>
                            {tradeForm.estimatedPrice && (
                                <div className="flex justify-between text-sm">
                                    <span className="text-dark-400">Current Price:</span>
                                    <span className="text-white font-mono">₹{tradeForm.estimatedPrice}</span>
                                </div>
                            )}
                        </div>

                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm text-dark-400 mb-1">Quantity</label>
                                <input
                                    type="number"
                                    className="input-field"
                                    value={tradeForm.quantity}
                                    onChange={(e) => setTradeForm({ ...tradeForm, quantity: parseInt(e.target.value) || 0 })}
                                    min="1"
                                />
                            </div>

                            <div>
                                <label className="block text-sm text-dark-400 mb-1">Order Type</label>
                                <select
                                    className="input-field"
                                    value="market"
                                    disabled
                                >
                                    <option value="market">Market Order</option>
                                    <option value="limit">Limit Order (Coming Soon)</option>
                                </select>
                            </div>

                            <div>
                                <label className="block text-sm text-dark-400 mb-1">Strategy</label>
                                <select
                                    className="input-field"
                                    value={tradeForm.strategy}
                                    onChange={(e) => setTradeForm({ ...tradeForm, strategy: e.target.value })}
                                >
                                    <option value="short-term">Short-term</option>
                                    <option value="long-term">Long-term</option>
                                </select>
                            </div>

                            {/* Order Summary */}
                            <div className="p-3 bg-dark-700/50 rounded border border-dark-600">
                                <div className="flex justify-between text-sm mb-1">
                                    <span className="text-dark-400">Estimated Total:</span>
                                    <span className="text-white font-bold">
                                        ₹{((tradeForm.estimatedPrice || 0) * (tradeForm.quantity || 0)).toLocaleString()}
                                    </span>
                                </div>
                                <p className="text-xs text-dark-500 mt-1">
                                    *Final execution price may vary slightly based on market movement.
                                </p>
                            </div>

                            <div className="flex gap-3 pt-4">
                                <button onClick={() => setShowTradeModal(false)} className="flex-1 btn-secondary">
                                    Cancel
                                </button>
                                <button
                                    onClick={handleTrade}
                                    className={`flex-1 ${tradeType === 'buy' ? 'btn-success' : 'btn-danger'}`}
                                    disabled={loading || !tradeForm.symbol || !tradeForm.quantity}
                                >
                                    {loading ? <RefreshCw className="w-4 h-4 animate-spin mx-auto" /> : (tradeType === 'buy' ? 'Confirm Buy' : 'Confirm Sell')}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

export default PaperTrading

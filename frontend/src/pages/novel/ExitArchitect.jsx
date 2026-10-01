import { useState, useEffect } from 'react'
import axios from 'axios'
import { LogOut, Target, Clock, TrendingDown, Layers } from 'lucide-react'

const API = 'http://localhost:8000/api/novel/exit'

export default function ExitArchitect() {
    const [symbol, setSymbol] = useState('')
    const [strategyType, setStrategyType] = useState('short-term')
    const [strategies, setStrategies] = useState(null)
    const [activeStrategies, setActiveStrategies] = useState([])
    const [loading, setLoading] = useState(false)
    const [tab, setTab] = useState('generate')

    useEffect(() => {
        if (tab === 'active') fetchActive()
    }, [tab])

    const generateStrategies = async () => {
        if (!symbol) return
        try {
            setLoading(true)
            const res = await axios.get(`${API}/${symbol.toUpperCase()}/strategies?strategy_type=${strategyType}`)
            setStrategies(res.data)
        } catch (err) { console.error(err) }
        finally { setLoading(false) }
    }

    const fetchActive = async () => {
        try {
            const res = await axios.get(`${API}/active`)
            setActiveStrategies(res.data)
        } catch (err) { console.error(err) }
    }

    const STRATEGY_ICONS = {
        profit_target: <Target className="w-5 h-5 text-green-400" />,
        technical: <TrendingDown className="w-5 h-5 text-blue-400" />,
        trailing_stop: <Clock className="w-5 h-5 text-amber-400" />,
        time_based: <Clock className="w-5 h-5 text-purple-400" />,
        support_resistance: <Layers className="w-5 h-5 text-cyan-400" />,
        fundamental_change: <TrendingDown className="w-5 h-5 text-red-400" />,
        valuation: <Target className="w-5 h-5 text-emerald-400" />,
        opportunity: <Target className="w-5 h-5 text-amber-400" />,
        rebalancing: <Layers className="w-5 h-5 text-violet-400" />,
        staged: <Layers className="w-5 h-5 text-pink-400" />,
    }

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                    <LogOut className="w-7 h-7 text-pink-400" />
                    Exit Strategy Architect
                </h1>
                <p className="text-dark-400 mt-1">AI-built custom exit strategies for every stock</p>
            </div>

            <div className="flex gap-2">
                <button onClick={() => setTab('generate')}
                    className={`px-4 py-2 rounded-lg font-medium transition-all ${tab === 'generate' ? 'bg-pink-600 text-white' : 'bg-dark-700 text-dark-400 hover:text-white'
                        }`}>🏗️ Generate</button>
                <button onClick={() => setTab('active')}
                    className={`px-4 py-2 rounded-lg font-medium transition-all ${tab === 'active' ? 'bg-pink-600 text-white' : 'bg-dark-700 text-dark-400 hover:text-white'
                        }`}>📋 Active Strategies</button>
            </div>

            {tab === 'generate' && (
                <>
                    <div className="glass-card p-5">
                        <div className="flex gap-3">
                            <input type="text" placeholder="Stock symbol (e.g. TCS)" value={symbol}
                                onChange={e => setSymbol(e.target.value)}
                                className="flex-1 bg-dark-700 border border-dark-600 rounded-lg px-4 py-2 text-white focus:border-pink-500 outline-none" />
                            <select value={strategyType} onChange={e => setStrategyType(e.target.value)}
                                className="bg-dark-700 border border-dark-600 rounded-lg px-4 py-2 text-white outline-none">
                                <option value="short-term">Short-Term</option>
                                <option value="long-term">Long-Term</option>
                            </select>
                            <button onClick={generateStrategies} disabled={loading}
                                className="px-6 py-2 bg-pink-600 text-white rounded-lg hover:bg-pink-500 disabled:opacity-50 transition-all font-medium">
                                {loading ? 'Generating...' : 'Generate'}
                            </button>
                        </div>
                    </div>

                    {strategies && (
                        <>
                            <div className="flex items-center justify-between">
                                <div>
                                    <span className="text-lg text-white font-semibold">{strategies.symbol}</span>
                                    <span className="text-dark-400 ml-3">₹{strategies.current_price}</span>
                                </div>
                                {strategies.recommendation && (
                                    <div className="bg-pink-500/20 px-3 py-1 rounded-full text-pink-400 text-sm">
                                        ⭐ Best: {strategies.recommendation.recommended}
                                    </div>
                                )}
                            </div>

                            <div className="space-y-4">
                                {(strategies.strategies || []).map((s, i) => (
                                    <div key={i} className={`glass-card p-5 ${strategies.recommendation?.recommended === s.name
                                            ? 'ring-1 ring-pink-500/50' : ''
                                        }`}>
                                        <div className="flex items-center gap-3 mb-3">
                                            {STRATEGY_ICONS[s.type] || <Target className="w-5 h-5 text-gray-400" />}
                                            <h3 className="text-white font-semibold">{s.name}</h3>
                                            {s.confidence && (
                                                <span className="ml-auto text-sm bg-dark-700 px-2 py-1 rounded text-dark-400">
                                                    {s.confidence}% confidence
                                                </span>
                                            )}
                                        </div>
                                        <p className="text-dark-400 text-sm mb-3">{s.description}</p>
                                        <div className="bg-dark-700/50 rounded-lg px-4 py-3">
                                            <span className="text-primary-400 text-sm font-mono">{s.trigger}</span>
                                        </div>
                                        {s.stop_loss && (
                                            <div className="mt-2 text-sm text-red-400">
                                                Stop-loss: ₹{s.stop_loss}
                                            </div>
                                        )}
                                        {s.stages && (
                                            <div className="mt-3 space-y-1">
                                                {s.stages.map((stage, si) => (
                                                    <div key={si} className="flex items-center justify-between text-sm">
                                                        <span className="text-dark-400">At {stage.at_return} return</span>
                                                        <span className="text-white">Sell {stage.sell_pct}</span>
                                                        {stage.price && <span className="text-dark-500">₹{stage.price}</span>}
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                        {s.triggers && (
                                            <ul className="mt-3 space-y-1">
                                                {s.triggers.map((t, ti) => (
                                                    <li key={ti} className="text-sm text-dark-400">• {t}</li>
                                                ))}
                                            </ul>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </>
                    )}
                </>
            )}

            {tab === 'active' && (
                <div className="space-y-3">
                    {activeStrategies.length === 0 ? (
                        <div className="glass-card p-8 text-center text-dark-400">
                            No active exit strategies. Generate some above!
                        </div>
                    ) : activeStrategies.map((s, i) => (
                        <div key={i} className="glass-card p-4 flex items-center justify-between">
                            <div>
                                <span className="text-white font-medium">{s.symbol}</span>
                                <span className="text-dark-400 text-sm ml-3">{s.strategy_type}</span>
                                <p className="text-dark-500 text-sm">{s.trigger_condition}</p>
                            </div>
                            <span className="text-xs text-dark-500">{s.created_at}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

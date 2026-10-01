import { useState, useEffect } from 'react'
import axios from 'axios'
import { Undo2, AlertTriangle, Lightbulb } from 'lucide-react'

const API = 'http://localhost:8000/api/novel/regret'

export default function RegretAnalyzer() {
    const [topRegrets, setTopRegrets] = useState([])
    const [patterns, setPatterns] = useState(null)
    const [tradeAnalysis, setTradeAnalysis] = useState(null)
    const [loading, setLoading] = useState(true)
    const [tradeId, setTradeId] = useState('')

    useEffect(() => {
        fetchData()
    }, [])

    const fetchData = async () => {
        try {
            setLoading(true)
            const [regrets, patternData] = await Promise.all([
                axios.get(`${API}/top-regrets`),
                axios.get(`${API}/patterns`)
            ])
            setTopRegrets(regrets.data)
            setPatterns(patternData.data)
        } catch (err) { console.error(err) }
        finally { setLoading(false) }
    }

    const analyzeTrade = async () => {
        if (!tradeId) return
        try {
            const res = await axios.get(`${API}/analyze/${tradeId}`)
            setTradeAnalysis(res.data)
        } catch (err) { console.error(err) }
    }

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                    <Undo2 className="w-7 h-7 text-orange-400" />
                    Trade Regret Analyzer
                </h1>
                <p className="text-dark-400 mt-1">Learn from your past decisions with counterfactual analysis</p>
            </div>

            {/* Analyze specific trade */}
            <div className="glass-card p-5">
                <h3 className="text-white font-semibold mb-3">Analyze a Trade</h3>
                <div className="flex gap-3">
                    <input type="number" placeholder="Enter Trade ID" value={tradeId}
                        onChange={e => setTradeId(e.target.value)}
                        className="flex-1 bg-dark-700 border border-dark-600 rounded-lg px-4 py-2 text-white focus:border-primary-500 outline-none" />
                    <button onClick={analyzeTrade}
                        className="px-6 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-500 transition-all font-medium">
                        Analyze
                    </button>
                </div>
            </div>

            {tradeAnalysis && (
                <div className="glass-card p-6">
                    <h3 className="text-white font-semibold mb-4">What-If Analysis</h3>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                        {Object.entries(tradeAnalysis.alternatives || {}).map(([key, alt]) => (
                            <div key={key} className="bg-dark-700/50 rounded-lg p-4">
                                <div className="text-sm text-dark-400 mb-1">{alt.strategy}</div>
                                <div className={`text-xl font-bold ${(alt.return_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                    {alt.return_pct?.toFixed(1)}%
                                </div>
                                {alt.exit_price && <div className="text-xs text-dark-500">₹{alt.exit_price}</div>}
                            </div>
                        ))}
                    </div>
                    <div className="flex items-center gap-2 text-amber-400">
                        <Lightbulb className="w-5 h-5" />
                        <span className="text-sm">{tradeAnalysis.lesson}</span>
                    </div>
                    <div className="mt-2 text-sm text-dark-400">
                        Regret Score: <span className="text-white font-bold">{tradeAnalysis.regret_score}</span>
                    </div>
                </div>
            )}

            {/* Patterns */}
            {patterns && (
                <div className="glass-card p-6">
                    <h3 className="text-white font-semibold mb-4 flex items-center gap-2">
                        <AlertTriangle className="w-5 h-5 text-amber-400" />
                        Your Trading Patterns
                    </h3>
                    <div className="grid grid-cols-2 gap-4 mb-4">
                        <div className="bg-dark-700/50 rounded-lg p-4 text-center">
                            <div className="text-2xl font-bold text-white">{patterns.total_analyzed}</div>
                            <div className="text-xs text-dark-400">Trades Analyzed</div>
                        </div>
                        <div className="bg-dark-700/50 rounded-lg p-4 text-center">
                            <div className="text-2xl font-bold text-orange-400">{patterns.avg_regret_score}</div>
                            <div className="text-xs text-dark-400">Avg Regret Score</div>
                        </div>
                    </div>
                    {patterns.common_mistakes?.map((m, i) => (
                        <p key={i} className="text-dark-400 text-sm mb-1">⚠️ {m}</p>
                    ))}
                    <p className="text-primary-400 text-sm mt-3">💡 {patterns.improvement_tip}</p>
                </div>
            )}

            {/* Top Regrets */}
            <div>
                <h3 className="text-lg font-semibold text-white mb-3">Top Regrets</h3>
                {loading ? (
                    <div className="text-dark-400">Loading...</div>
                ) : topRegrets.length === 0 ? (
                    <div className="glass-card p-8 text-center text-dark-400">
                        No trades to analyze yet. Start paper trading to see your regret analysis!
                    </div>
                ) : (
                    <div className="space-y-2">
                        {topRegrets.map((r, i) => (
                            <div key={i} className="glass-card p-4 flex items-center justify-between">
                                <div>
                                    <span className="text-white font-medium">{r.symbol}</span>
                                    <span className="text-dark-400 text-sm ml-2">Trade #{r.trade_id}</span>
                                    <p className="text-dark-500 text-sm">{r.lesson}</p>
                                </div>
                                <div className={`text-lg font-bold ${r.regret_score > 10 ? 'text-red-400' : 'text-amber-400'}`}>
                                    {r.regret_score?.toFixed(1)}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    )
}

import { useState, useEffect } from 'react'
import axios from 'axios'
import { Users, TrendingUp, Eye, Plus } from 'lucide-react'

const API = 'http://localhost:8000/api/novel/trade-clone'

export default function TradeClone() {
    const [portfolios, setPortfolios] = useState([])
    const [selectedPortfolio, setSelectedPortfolio] = useState(null)
    const [analysis, setAnalysis] = useState(null)
    const [recommendations, setRecommendations] = useState([])
    const [loading, setLoading] = useState(true)
    const [tab, setTab] = useState('portfolios')

    useEffect(() => {
        fetchPortfolios()
    }, [])

    const fetchPortfolios = async () => {
        try {
            setLoading(true)
            const res = await axios.get(`${API}/portfolios`)
            setPortfolios(res.data)
        } catch (err) { console.error(err) }
        finally { setLoading(false) }
    }

    const fetchAnalysis = async (id) => {
        try {
            const res = await axios.get(`${API}/portfolios/${id}/analysis`)
            setAnalysis(res.data)
            setSelectedPortfolio(id)
        } catch (err) { console.error(err) }
    }

    const fetchRecommendations = async () => {
        try {
            setLoading(true)
            const res = await axios.get(`${API}/recommendations`)
            setRecommendations(res.data)
        } catch (err) { console.error(err) }
        finally { setLoading(false) }
    }

    return (
        <div className="p-6 space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                        <Users className="w-7 h-7 text-primary-400" />
                        Trade Clone System
                    </h1>
                    <p className="text-dark-400 mt-1">Replicate strategies of successful investors</p>
                </div>
            </div>

            {/* Tabs */}
            <div className="flex gap-2">
                {['portfolios', 'recommendations'].map(t => (
                    <button key={t} onClick={() => { setTab(t); if (t === 'recommendations') fetchRecommendations() }}
                        className={`px-4 py-2 rounded-lg font-medium transition-all ${tab === t ? 'bg-primary-600 text-white' : 'bg-dark-700 text-dark-400 hover:text-white'
                            }`}>
                        {t === 'portfolios' ? '📊 Tracked Portfolios' : '🎯 Clone Picks'}
                    </button>
                ))}
            </div>

            {tab === 'portfolios' && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {loading ? (
                        <div className="col-span-2 text-center text-dark-400 py-12">Loading portfolios...</div>
                    ) : portfolios.map(p => (
                        <div key={p.id} className="glass-card p-5 hover:border-primary-500/30 transition-all cursor-pointer"
                            onClick={() => fetchAnalysis(p.id)}>
                            <div className="flex items-start justify-between mb-3">
                                <h3 className="text-lg font-semibold text-white">{p.portfolio_name}</h3>
                                <span className="px-2 py-1 text-xs rounded-full bg-primary-500/20 text-primary-400">
                                    {p.investor_type}
                                </span>
                            </div>
                            <p className="text-dark-400 text-sm mb-4">{p.description}</p>
                            <div className="flex gap-4 text-sm">
                                <span className="text-dark-500">Since: {p.tracked_since}</span>
                                <button className="text-primary-400 hover:text-primary-300 flex items-center gap-1">
                                    <Eye className="w-4 h-4" /> Analyze
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {analysis && tab === 'portfolios' && (
                <div className="glass-card p-6 mt-4">
                    <h3 className="text-lg font-semibold text-white mb-4">Portfolio Analysis</h3>
                    <p className="text-dark-400 mb-4">{analysis.strategy_summary}</p>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                        <div className="bg-dark-700/50 rounded-lg p-3 text-center">
                            <div className="text-2xl font-bold text-white">{analysis.total_holdings}</div>
                            <div className="text-xs text-dark-400">Holdings</div>
                        </div>
                        {Object.entries(analysis.sector_allocation || {}).map(([sector, count]) => (
                            <div key={sector} className="bg-dark-700/50 rounded-lg p-3 text-center">
                                <div className="text-lg font-bold text-primary-400">{count}</div>
                                <div className="text-xs text-dark-400">{sector}</div>
                            </div>
                        ))}
                    </div>
                    <div className="space-y-2">
                        {(analysis.holdings || []).map((h, i) => (
                            <div key={i} className="flex items-center justify-between py-2 border-b border-dark-700/50">
                                <span className="text-white font-medium">{h.symbol}</span>
                                <span className={`text-sm ${(h.change_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                    {h.current_price ? `₹${h.current_price}` : '—'} {h.change_pct ? `(${h.change_pct}%)` : ''}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {tab === 'recommendations' && (
                <div className="space-y-3">
                    {loading ? (
                        <div className="text-center text-dark-400 py-12">Generating clone picks...</div>
                    ) : recommendations.length === 0 ? (
                        <div className="text-center text-dark-400 py-12">No recommendations yet. Try tracking more portfolios.</div>
                    ) : recommendations.map((r, i) => (
                        <div key={i} className="glass-card p-5 flex items-center justify-between">
                            <div>
                                <span className="text-white font-semibold text-lg">{r.symbol}</span>
                                <span className="text-dark-400 text-sm ml-3">{r.name}</span>
                                <p className="text-dark-500 text-sm mt-1">Cloned from: {r.clone_source}</p>
                                <p className="text-dark-400 text-sm">{r.reasoning}</p>
                            </div>
                            <div className="text-right">
                                <div className="text-2xl font-bold text-primary-400">{r.match_score}%</div>
                                <div className="text-xs text-dark-500">Match</div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

import { useState, useEffect } from 'react'
import axios from 'axios'
import { Activity, TrendingUp, TrendingDown, Gauge } from 'lucide-react'

const API = 'http://localhost:8000/api/novel/mood'

const MOOD_COLORS = {
    'EXTREME FEAR 😱': { bg: 'from-red-600 to-red-800', ring: 'ring-red-500' },
    'FEAR 😰': { bg: 'from-red-500 to-orange-600', ring: 'ring-orange-500' },
    'CAUTIOUS 🤔': { bg: 'from-orange-500 to-yellow-500', ring: 'ring-yellow-500' },
    'NEUTRAL 😐': { bg: 'from-gray-500 to-gray-600', ring: 'ring-gray-400' },
    'OPTIMISM 😊': { bg: 'from-green-600 to-emerald-500', ring: 'ring-green-500' },
    'GREED 🤑': { bg: 'from-emerald-500 to-teal-500', ring: 'ring-teal-500' },
    'EXTREME GREED 🔥': { bg: 'from-amber-500 to-yellow-400', ring: 'ring-yellow-400' },
}

export default function MoodRing() {
    const [mood, setMood] = useState(null)
    const [history, setHistory] = useState([])
    const [loading, setLoading] = useState(true)

    useEffect(() => { fetchMood() }, [])

    const fetchMood = async () => {
        try {
            setLoading(true)
            const [moodRes, histRes] = await Promise.all([
                axios.get(`${API}/current`),
                axios.get(`${API}/history?limit=15`)
            ])
            setMood(moodRes.data)
            setHistory(histRes.data)
        } catch (err) { console.error(err) }
        finally { setLoading(false) }
    }

    const moodStyle = mood ? MOOD_COLORS[mood.mood] || MOOD_COLORS['NEUTRAL 😐'] : MOOD_COLORS['NEUTRAL 😐']

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                    <Activity className="w-7 h-7 text-purple-400" />
                    Market Mood Ring
                </h1>
                <p className="text-dark-400 mt-1">Real-time emotion detection in the market</p>
            </div>

            {loading ? (
                <div className="text-center text-dark-400 py-16">Reading market emotions...</div>
            ) : mood && (
                <>
                    {/* Main Mood Display */}
                    <div className={`glass-card p-8 text-center bg-gradient-to-br ${moodStyle.bg} bg-opacity-20`}>
                        <div className="text-6xl mb-4">{mood.mood?.split(' ').pop()}</div>
                        <h2 className="text-3xl font-bold text-white mb-2">{mood.mood}</h2>
                        <div className="text-7xl font-black text-white my-6">
                            {mood.fear_greed_index?.toFixed(0)}
                        </div>
                        <p className="text-sm text-white/70">Fear & Greed Index (0 = Extreme Fear, 100 = Extreme Greed)</p>
                    </div>

                    {/* Recommendation */}
                    <div className="glass-card p-5">
                        <h3 className="text-white font-semibold mb-2">💡 Investment Recommendation</h3>
                        <p className="text-lg text-dark-300">{mood.recommendation}</p>
                    </div>

                    {/* Indicators */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <IndicatorCard
                            title="India VIX (Fear Gauge)"
                            value={mood.indicators?.india_vix?.value}
                            change={mood.indicators?.india_vix?.change_pct}
                            subtitle={mood.indicators?.india_vix?.level}
                        />
                        <IndicatorCard
                            title="Nifty 50"
                            value={mood.indicators?.nifty?.value}
                            change={mood.indicators?.nifty?.change_pct}
                        />
                        <IndicatorCard
                            title="FII/DII Flow"
                            value={mood.indicators?.fii_dii?.fii_net}
                            subtitle={mood.indicators?.fii_dii?.source === 'unavailable' ? 'Data unavailable' : 'FII Net Flow'}
                        />
                    </div>

                    {/* History */}
                    {history.length > 0 && (
                        <div className="glass-card p-6">
                            <h3 className="text-white font-semibold mb-4">Mood History</h3>
                            <div className="space-y-2">
                                {history.map((h, i) => (
                                    <div key={i} className="flex items-center justify-between py-2 border-b border-dark-700/30">
                                        <span className="text-dark-400 text-sm">{new Date(h.timestamp).toLocaleDateString()}</span>
                                        <span className="text-white">{h.mood}</span>
                                        <span className="text-primary-400 font-mono">{h.fear_greed_index?.toFixed(0)}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    )
}

function IndicatorCard({ title, value, change, subtitle }) {
    return (
        <div className="glass-card p-5">
            <div className="text-sm text-dark-400 mb-1">{title}</div>
            <div className="text-2xl font-bold text-white">
                {value != null ? (typeof value === 'number' ? value.toLocaleString() : value) : '—'}
            </div>
            {change != null && (
                <div className={`flex items-center gap-1 text-sm mt-1 ${change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {change >= 0 ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
                    {change?.toFixed(2)}%
                </div>
            )}
            {subtitle && <div className="text-xs text-dark-500 mt-1">{subtitle}</div>}
        </div>
    )
}

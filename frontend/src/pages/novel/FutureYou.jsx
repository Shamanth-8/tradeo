import { useState } from 'react'
import axios from 'axios'
import { Rocket, TrendingUp, PiggyBank, Target } from 'lucide-react'

const API = 'http://localhost:8000/api/novel/future'

export default function FutureYou() {
    const [params, setParams] = useState({
        starting_capital: 500000,
        monthly_sip: 10000,
        years_forward: 10,
        expected_return: 12,
        volatility: 18,
    })
    const [result, setResult] = useState(null)
    const [loading, setLoading] = useState(false)

    const runSimulation = async () => {
        try {
            setLoading(true)
            const res = await axios.post(`${API}/simulate`, params)
            setResult(res.data)
        } catch (err) { console.error(err) }
        finally { setLoading(false) }
    }

    const formatINR = (val) => {
        if (!val) return '—'
        if (val >= 10000000) return `₹${(val / 10000000).toFixed(1)} Cr`
        if (val >= 100000) return `₹${(val / 100000).toFixed(1)} L`
        return `₹${val.toLocaleString()}`
    }

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                    <Rocket className="w-7 h-7 text-cyan-400" />
                    Future You Simulator
                </h1>
                <p className="text-dark-400 mt-1">Monte Carlo simulations: See where your investments could take you</p>
            </div>

            {/* Input Form */}
            <div className="glass-card p-6">
                <h3 className="text-white font-semibold mb-4">Simulation Parameters</h3>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <InputField label="Starting Capital (₹)" value={params.starting_capital}
                        onChange={v => setParams(p => ({ ...p, starting_capital: Number(v) }))} />
                    <InputField label="Monthly SIP (₹)" value={params.monthly_sip}
                        onChange={v => setParams(p => ({ ...p, monthly_sip: Number(v) }))} />
                    <InputField label="Years Forward" value={params.years_forward}
                        onChange={v => setParams(p => ({ ...p, years_forward: Number(v) }))} />
                    <InputField label="Expected Return (%)" value={params.expected_return}
                        onChange={v => setParams(p => ({ ...p, expected_return: Number(v) }))} />
                    <InputField label="Volatility (%)" value={params.volatility}
                        onChange={v => setParams(p => ({ ...p, volatility: Number(v) }))} />
                </div>
                <button onClick={runSimulation} disabled={loading}
                    className="mt-4 w-full py-3 bg-cyan-600 text-white rounded-lg font-semibold hover:bg-cyan-500 disabled:opacity-50 transition-all">
                    {loading ? '🔮 Simulating 10,000 futures...' : '🚀 Simulate My Future'}
                </button>
            </div>

            {result && (
                <>
                    {/* Outcomes */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <OutcomeCard title="Pessimistic" icon="😟" percentile="10th"
                            value={formatINR(result.outcomes?.pessimistic?.value)}
                            cagr={result.outcomes?.pessimistic?.cagr}
                            color="text-red-400" />
                        <OutcomeCard title="Realistic" icon="📊" percentile="50th"
                            value={formatINR(result.outcomes?.realistic?.value)}
                            cagr={result.outcomes?.realistic?.cagr}
                            color="text-green-400" highlighted />
                        <OutcomeCard title="Optimistic" icon="🚀" percentile="90th"
                            value={formatINR(result.outcomes?.optimistic?.value)}
                            cagr={result.outcomes?.optimistic?.cagr}
                            color="text-cyan-400" />
                    </div>

                    {/* Statistics */}
                    <div className="glass-card p-6">
                        <h3 className="text-white font-semibold mb-4">Key Statistics</h3>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <StatItem label="Total Invested" value={formatINR(result.statistics?.total_invested)} />
                            <StatItem label="Median Outcome" value={formatINR(result.statistics?.median)} />
                            <StatItem label="Mean Outcome" value={formatINR(result.statistics?.mean)} />
                            <StatItem label="Probability of Loss" value={`${result.statistics?.probability_of_loss?.toFixed(1)}%`}
                                highlight={result.statistics?.probability_of_loss > 20} />
                        </div>
                    </div>

                    {/* Milestones */}
                    {result.milestones?.length > 0 && (
                        <div className="glass-card p-6">
                            <h3 className="text-white font-semibold mb-4 flex items-center gap-2">
                                <Target className="w-5 h-5 text-amber-400" />
                                Milestones
                            </h3>
                            <div className="space-y-3">
                                {result.milestones.map((m, i) => (
                                    <div key={i} className="flex items-center justify-between py-2 border-b border-dark-700/30">
                                        <span className="text-white font-medium">{m.target_label}</span>
                                        <span className="text-primary-400 font-mono">
                                            ~{typeof m.estimated_years === 'number' ? `${m.estimated_years} years` : m.estimated_years}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Distribution */}
                    {result.distribution?.length > 0 && (
                        <div className="glass-card p-6">
                            <h3 className="text-white font-semibold mb-4">Outcome Distribution</h3>
                            <div className="space-y-1">
                                {result.distribution.map((d, i) => (
                                    <div key={i} className="flex items-center gap-3">
                                        <span className="text-xs text-dark-500 w-28 text-right">{formatINR(d.range_start)}</span>
                                        <div className="flex-1 bg-dark-700 rounded-full h-5 overflow-hidden">
                                            <div className="bg-gradient-to-r from-cyan-600 to-cyan-400 h-full rounded-full transition-all"
                                                style={{ width: `${Math.min(d.percentage * 5, 100)}%` }} />
                                        </div>
                                        <span className="text-xs text-dark-400 w-12">{d.percentage}%</span>
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

function InputField({ label, value, onChange }) {
    return (
        <div>
            <label className="text-sm text-dark-400 mb-1 block">{label}</label>
            <input type="number" value={value} onChange={e => onChange(e.target.value)}
                className="w-full bg-dark-700 border border-dark-600 rounded-lg px-4 py-2 text-white focus:border-cyan-500 outline-none" />
        </div>
    )
}

function OutcomeCard({ title, icon, percentile, value, cagr, color, highlighted }) {
    return (
        <div className={`glass-card p-6 text-center ${highlighted ? 'ring-2 ring-green-500/50' : ''}`}>
            <div className="text-3xl mb-2">{icon}</div>
            <div className="text-sm text-dark-400">{title} ({percentile} percentile)</div>
            <div className={`text-3xl font-bold mt-2 ${color}`}>{value}</div>
            {cagr != null && <div className="text-sm text-dark-500 mt-1">CAGR: {cagr?.toFixed(1)}%</div>}
        </div>
    )
}

function StatItem({ label, value, highlight }) {
    return (
        <div className="bg-dark-700/50 rounded-lg p-3 text-center">
            <div className={`text-lg font-bold ${highlight ? 'text-red-400' : 'text-white'}`}>{value}</div>
            <div className="text-xs text-dark-400">{label}</div>
        </div>
    )
}

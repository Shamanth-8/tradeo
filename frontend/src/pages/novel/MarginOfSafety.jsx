import { useState } from 'react'
import axios from 'axios'
import { Shield, Search, ArrowDown, ArrowUp, Minus } from 'lucide-react'

const API = 'http://localhost:8000/api/novel/margin'

export default function MarginOfSafety() {
    const [symbol, setSymbol] = useState('')
    const [result, setResult] = useState(null)
    const [loading, setLoading] = useState(false)

    const calculate = async () => {
        if (!symbol) return
        try {
            setLoading(true)
            const res = await axios.get(`${API}/${symbol.toUpperCase()}`)
            setResult(res.data)
        } catch (err) { console.error(err) }
        finally { setLoading(false) }
    }

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                    <Shield className="w-7 h-7 text-emerald-400" />
                    Margin of Safety Calculator
                </h1>
                <p className="text-dark-400 mt-1">Benjamin Graham's philosophy automated with AI risk scoring</p>
            </div>

            {/* Search */}
            <div className="glass-card p-5">
                <div className="flex gap-3">
                    <input type="text" placeholder="Enter stock symbol (e.g. TCS, RELIANCE)" value={symbol}
                        onChange={e => setSymbol(e.target.value)} onKeyDown={e => e.key === 'Enter' && calculate()}
                        className="flex-1 bg-dark-700 border border-dark-600 rounded-lg px-4 py-3 text-white focus:border-emerald-500 outline-none text-lg" />
                    <button onClick={calculate} disabled={loading}
                        className="px-8 py-3 bg-emerald-600 text-white rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-all font-semibold">
                        {loading ? 'Calculating...' : 'Calculate'}
                    </button>
                </div>
            </div>

            {result && (
                <>
                    {/* Main Result */}
                    <div className={`glass-card p-8 text-center ${result.margin_of_safety_pct > 0 ? 'ring-1 ring-emerald-500/30' : 'ring-1 ring-red-500/30'}`}>
                        <div className="text-dark-400 mb-2">Current Price</div>
                        <div className="text-4xl font-bold text-white mb-4">₹{result.current_price?.toLocaleString()}</div>

                        <div className="flex items-center justify-center gap-2 mb-2">
                            {result.margin_of_safety_pct > 0 ? (
                                <ArrowDown className="w-6 h-6 text-emerald-400" />
                            ) : result.margin_of_safety_pct < 0 ? (
                                <ArrowUp className="w-6 h-6 text-red-400" />
                            ) : (
                                <Minus className="w-6 h-6 text-gray-400" />
                            )}
                            <span className={`text-5xl font-black ${result.margin_of_safety_pct > 15 ? 'text-emerald-400' :
                                    result.margin_of_safety_pct > 0 ? 'text-green-400' :
                                        result.margin_of_safety_pct > -15 ? 'text-amber-400' : 'text-red-400'
                                }`}>
                                {result.margin_of_safety_pct?.toFixed(1)}%
                            </span>
                        </div>
                        <div className="text-sm text-dark-400 mb-4">Margin of Safety</div>

                        <div className="text-lg">{result.verdict}</div>

                        <div className="mt-4 inline-flex items-center gap-2 bg-dark-700/50 px-4 py-2 rounded-full">
                            <span className="text-dark-400 text-sm">Risk Score:</span>
                            <span className={`font-bold ${result.risk_score <= 4 ? 'text-green-400' : result.risk_score <= 6 ? 'text-amber-400' : 'text-red-400'}`}>
                                {result.risk_score}/10
                            </span>
                        </div>
                    </div>

                    {/* Valuation Methods */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        {Object.entries(result.valuation_methods || {}).map(([key, method]) => (
                            <div key={key} className="glass-card p-5">
                                <div className="text-sm text-dark-400 mb-1">
                                    {key === 'graham_formula' ? '📐 Graham Formula' :
                                        key === 'simplified_dcf' ? '📊 Simplified DCF' : '💰 Earnings Power'}
                                </div>
                                <div className="text-2xl font-bold text-white mb-2">
                                    {method.intrinsic_value ? `₹${method.intrinsic_value.toLocaleString()}` : '—'}
                                </div>
                                <p className="text-xs text-dark-500">{method.method}</p>
                                <p className="text-xs text-dark-600 mt-1">{method.assumptions}</p>
                            </div>
                        ))}
                    </div>

                    {/* Average Intrinsic Value */}
                    <div className="glass-card p-5 text-center">
                        <div className="text-sm text-dark-400 mb-1">Average Intrinsic Value (across all methods)</div>
                        <div className="text-3xl font-bold text-emerald-400">
                            ₹{result.avg_intrinsic_value?.toLocaleString()}
                        </div>
                    </div>
                </>
            )}
        </div>
    )
}

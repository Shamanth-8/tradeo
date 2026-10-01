import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CalendarRange, RefreshCw } from 'lucide-react'
import { Loading } from '../components/hud/HudPanel'
import { autopilotApi } from '../services/api'

/**
 * Long-term picks by holding period. Lists, not orders: each horizon uses the
 * rule tested for it and says how that rule did. Quick trades (intraday,
 * swing) are agents on the Autopilot page.
 */

const ORDER = ['1m', '6m', '1y', '1y+']

export default function LongTerm() {
    const [data, setData] = useState(null)
    const [tab, setTab] = useState('1m')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState(null)

    const load = useCallback(async (refresh = false) => {
        setBusy(true)
        setError(null)
        try {
            const { data } = await autopilotApi.longterm(refresh)
            setData(data)
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        } finally {
            setBusy(false)
        }
    }, [])
    useEffect(() => { load() }, [load])

    const h = data?.horizons?.[tab]

    return (
        <div className="space-y-5">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="font-mono text-lg uppercase tracking-[0.3em] text-primary-300 text-glow">Long-term picks</h1>
                    <p className="mt-1 text-xs text-dark-400">
                        Stocks for each holding period, ranked by the rule tested for it. Quick trades (same-day and up to two
                        weeks) are agents on the <Link to="/autopilot" className="underline">Autopilot</Link> page.
                    </p>
                </div>
                <button onClick={() => load(true)} disabled={busy} className="btn-ghost flex items-center gap-2">
                    <RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} /> Refresh
                </button>
            </header>

            <div className="flex flex-wrap gap-2">
                {ORDER.map((k) => (
                    <button key={k} onClick={() => setTab(k)} className={tab === k ? 'btn-primary' : 'btn-secondary'}>
                        {data?.horizons?.[k]?.label || { '1m': '1 month', '6m': '6 months', '1y': '1 year', '1y+': 'More than 1 year' }[k]}
                    </button>
                ))}
            </div>

            {error && <p className="text-sm text-danger-400">{error}</p>}
            {!data && !error && <Loading rows={4} label="Ranking stocks (first load takes up to a minute)" />}

            {h && (
                <div className="glass-card space-y-4 p-6">
                    <div className="flex items-start gap-3">
                        <CalendarRange className="mt-0.5 h-5 w-5 text-primary-400" />
                        <div>
                            <p className="font-semibold text-white">{h.label}</p>
                            <p className="text-sm text-dark-300">{h.rule}</p>
                            <p className="mt-1 text-xs text-dark-400">{h.evidence}</p>
                        </div>
                    </div>
                    {h.picks.length === 0 ? (
                        <p className="text-sm text-dark-400">No stock passes this rule right now — that itself is information (a weak market).</p>
                    ) : (
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-dark-700 text-left text-xs text-dark-400">
                                    <th className="p-2">#</th><th className="p-2">Stock</th><th className="p-2 text-right">Price</th>
                                    <th className="p-2">Why it's here</th><th className="p-2">Also</th>
                                </tr>
                            </thead>
                            <tbody>
                                {h.picks.map((p, i) => (
                                    <tr key={p.symbol} className="border-b border-dark-800/60">
                                        <td className="p-2 text-dark-500">{i + 1}</td>
                                        <td className="p-2">
                                            <Link to={`/stock/${p.symbol}`} className="font-semibold text-white hover:underline">{p.symbol}</Link>
                                            <span className="ml-2 text-xs text-dark-500">{p.name}</span>
                                        </td>
                                        <td className="p-2 text-right text-dark-200">₹{Number(p.price).toLocaleString('en-IN')}</td>
                                        <td className="p-2 text-primary-300">{p.metric}</td>
                                        <td className="p-2 text-xs text-dark-400">{p.detail}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                    <p className="flex items-start gap-1.5 text-[11px] text-dark-500">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0" /> {data.caveat} Updated {data.generated_at}.
                    </p>
                </div>
            )}
        </div>
    )
}

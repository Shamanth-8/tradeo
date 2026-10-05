import { useEffect, useState } from 'react'
import { FlaskConical, RefreshCw } from 'lucide-react'
import { HudPanel, Loading } from './hud/HudPanel'
import { autopilotApi } from '../services/api'

/**
 * Every strategy against the Nifty 50 and no-skill controls (pipeline/evaluate.py),
 * on the period its rules were chosen on and on a held-out period they never saw.
 */

const num = (v, digits = 1) => (v === null || v === undefined ? '—' : Number(v).toFixed(digits))
const tone = (v) => (v > 0 ? 'text-success-400' : v < 0 ? 'text-danger-400' : 'text-dark-300')

export default function EvaluationPanel() {
    const [report, setReport] = useState(null)
    const [period, setPeriod] = useState('holdout')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState(null)

    useEffect(() => {
        autopilotApi.evaluation().then(({ data }) => setReport(data)).catch((err) => setError(err.message))
    }, [])

    const rerun = async () => {
        setBusy(true)
        setError(null)
        try {
            const { data } = await autopilotApi.runEvaluation()
            setReport(data)
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        } finally {
            setBusy(false)
        }
    }

    if (!report && !error) return <Loading rows={2} label="Strategy evaluation" />

    const rows = report?.results || []
    return (
        <HudPanel
            title="How the strategies tested"
            subtitle={report?.generated_at
                ? `Portfolio simulation with Indian charges and size-aware slippage. Rules were chosen on ${report.in_sample}; ${report.holdout} is held out. Run ${report.generated_at}.`
                : 'Not run yet.'}
            right={
                <div className="flex items-center gap-2">
                    {['in_sample', 'holdout'].map((p) => (
                        <button key={p} onClick={() => setPeriod(p)}
                            className={`${period === p ? 'btn-primary' : 'btn-ghost'} !px-2 !py-1 !text-[10px]`}>
                            {p === 'holdout' ? 'Held out (2025+)' : 'In sample (2020–24)'}
                        </button>
                    ))}
                    <button onClick={rerun} disabled={busy} className="btn-ghost !px-2 !py-1 !text-[10px]">
                        {busy ? <RefreshCw className="h-3 w-3 animate-spin" /> : <FlaskConical className="h-3 w-3" />}
                        {busy ? 'Running…' : 'Re-run'}
                    </button>
                </div>
            }
        >
            {error && <p className="mb-2 text-xs text-danger-300">{error}</p>}
            {rows.length > 0 && (
                <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                        <thead className="text-left text-dark-400">
                            <tr>
                                <th className="py-1 pr-3 font-normal">Strategy</th>
                                <th className="pr-3 text-right font-normal">CAGR</th>
                                <th className="pr-3 text-right font-normal">Nifty</th>
                                <th className="pr-3 text-right font-normal">Sharpe</th>
                                <th className="pr-3 text-right font-normal">Worst drop</th>
                                <th className="pr-3 text-right font-normal">Invested</th>
                                <th className="pr-3 text-right font-normal">Trades</th>
                                <th className="text-right font-normal">Win rate</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r) => {
                                const m = r[period] || {}
                                const control = r.note === 'no-skill control'
                                return (
                                    <tr key={r.name} className={`border-t border-dark-800/60 ${control ? 'text-dark-400' : 'text-dark-200'}`}>
                                        <td className="py-1.5 pr-3">{r.name}<span className="block text-[10px] text-dark-500">{r.note}</span></td>
                                        <td className={`pr-3 text-right font-mono ${tone(m.cagr_pct)}`}>{num(m.cagr_pct)}%</td>
                                        <td className="pr-3 text-right font-mono text-dark-400">{num(m.nifty_cagr_pct)}%</td>
                                        <td className="pr-3 text-right font-mono">{num(m.sharpe, 2)}</td>
                                        <td className="pr-3 text-right font-mono text-danger-300">{num(m.max_drawdown_pct, 0)}%</td>
                                        <td className="pr-3 text-right font-mono">{num(m.exposure_pct, 0)}%</td>
                                        <td className="pr-3 text-right font-mono">{r.name.startsWith('Buy all') ? '—' : m.trades ?? '—'}</td>
                                        <td className="text-right font-mono">{r.name.startsWith('Buy all') ? '—' : `${num(m.win_rate_pct, 0)}%`}</td>
                                    </tr>
                                )
                            })}
                        </tbody>
                    </table>
                </div>
            )}
            {report?.caveats && (
                <ul className="mt-3 space-y-1 text-[11px] text-dark-500">
                    {report.caveats.map((c) => <li key={c}>• {c}</li>)}
                </ul>
            )}
        </HudPanel>
    )
}

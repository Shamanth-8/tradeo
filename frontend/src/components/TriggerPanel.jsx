import { useCallback, useEffect, useState } from 'react'
import { Crosshair, Target, TrendingDown, X, Zap } from 'lucide-react'
import { autopilotApi } from '../services/api'

/**
 * Open paper brackets, and the record they leave behind.
 *
 * The bar between stop and target is the point of this panel: a position is
 * always somewhere on a line it committed to before entry, so "how is this
 * doing" has a visual answer that cannot be rationalised after the fact.
 *
 * Gapped exits are called out explicitly in the history. A stop that did not
 * fill at its level is the difference between the strategy you tested and the
 * one you actually traded, and hiding it is how a paper record flatters itself.
 */

const EXIT_TONE = {
    target_hit: 'text-success-300',
    stopped_out: 'text-danger-300',
    expired: 'text-dark-400',
    cancelled: 'text-dark-500',
}

export default function TriggerPanel() {
    const [active, setActive] = useState([])
    const [status, setStatus] = useState(null)
    const [history, setHistory] = useState([])
    const [performance, setPerformance] = useState(null)

    const load = useCallback(async () => {
        try {
            const [a, h] = await Promise.all([
                autopilotApi.triggers(),
                autopilotApi.triggerHistory(20),
            ])
            setActive(a.data.active || [])
            setStatus(a.data.status)
            setHistory(h.data.history || [])
            setPerformance(h.data.performance)
        } catch {
            /* the panel is supplementary — a failure here must not blank the page */
        }
    }, [])

    useEffect(() => {
        load()
        const timer = setInterval(load, 10000)
        return () => clearInterval(timer)
    }, [load])

    const cancel = async (id) => {
        try {
            await autopilotApi.cancelTrigger(id)
            load()
        } catch { /* ignore */ }
    }

    return (
        <section className="hud-panel p-4">
            <header className="mb-3 flex flex-wrap items-center gap-3">
                <Crosshair size={14} className="text-primary-300" />
                <h2 className="hud-title">Paper brackets</h2>
                {status && (
                    <span className="type-caption vibrant-tertiary">
                        {status.tick_driven ? 'tick-driven' : 'polling'} ·{' '}
                        {status.armed_today}/{status.caps.max_per_day} opened today by the agents above ·
                        each closes at its stop or target
                    </span>
                )}
                {performance?.trades > 0 && (
                    <span className="type-caption ml-auto vibrant-secondary">
                        {performance.trades} closed · {performance.win_rate}% won ·{' '}
                        <span className={performance.realised_pnl >= 0 ? 'text-success-300' : 'text-danger-300'}>
                            ₹{Number(performance.realised_pnl).toLocaleString('en-IN')}
                        </span>
                    </span>
                )}
            </header>

            {active.length === 0 && (
                <p className="type-body vibrant-tertiary py-4 text-center">
                    No open brackets. A signal above {status?.caps?.strong_conviction ?? 80}%
                    conviction with a stop and target arms one automatically.
                </p>
            )}

            <div className="space-y-2">
                {active.map((trigger) => {
                    const progress = Math.max(0, Math.min(100, trigger.progress_pct ?? 0))
                    const up = (trigger.unrealised_pnl ?? 0) >= 0
                    return (
                        <div key={trigger.id} className="material-raised rounded-lg p-3">
                            <div className="flex flex-wrap items-baseline gap-x-2.5">
                                <span className="type-body vibrant font-medium">{trigger.symbol}</span>
                                <span className="type-caption vibrant-tertiary type-numeric">
                                    {trigger.quantity} @ ₹{Number(trigger.entry_price).toLocaleString('en-IN')}
                                </span>
                                <span className="type-caption vibrant-tertiary">
                                    {trigger.risk_reward}:1
                                </span>
                                {trigger.ltp && (
                                    <span className={`type-caption type-numeric ml-auto ${up ? 'text-success-300' : 'text-danger-300'}`}>
                                        ₹{Number(trigger.unrealised_pnl).toLocaleString('en-IN')}
                                        <span className="ml-1 opacity-70">
                                            ({trigger.return_pct > 0 ? '+' : ''}{trigger.return_pct}%)
                                        </span>
                                    </span>
                                )}
                                <button
                                    onClick={() => cancel(trigger.id)}
                                    title="Square off at market"
                                    className="press vibrant-tertiary rounded p-0.5 hover:text-danger-300"
                                >
                                    <X size={12} />
                                </button>
                            </div>

                            {/* Stop ── position ── target, on one line. */}
                            <div className="mt-2.5">
                                <div className="relative h-1.5 overflow-hidden rounded-full bg-dark-800">
                                    <div
                                        className="absolute inset-y-0 left-0 bg-gradient-to-r from-danger-500/50 via-dark-600 to-success-500/50"
                                        style={{ width: '100%' }}
                                    />
                                    <div
                                        className="absolute top-0 h-1.5 w-0.5 bg-white shadow-glow"
                                        style={{ left: `${progress}%` }}
                                    />
                                </div>
                                <div className="mt-1 flex justify-between">
                                    <span className="type-caption text-danger-300/80 type-numeric">
                                        <TrendingDown size={9} className="mr-0.5 inline" />
                                        ₹{Number(trigger.stop_loss).toLocaleString('en-IN')}
                                    </span>
                                    <span className="type-caption vibrant-tertiary type-numeric">
                                        {trigger.ltp ? `₹${Number(trigger.ltp).toLocaleString('en-IN')}` : '—'}
                                    </span>
                                    <span className="type-caption text-success-300/80 type-numeric">
                                        ₹{Number(trigger.target).toLocaleString('en-IN')}
                                        <Target size={9} className="ml-0.5 inline" />
                                    </span>
                                </div>
                            </div>
                        </div>
                    )
                })}
            </div>

            {history.length > 0 && (
                <div className="mt-4">
                    <p className="type-label vibrant-tertiary mb-1.5">Closed</p>
                    <div className="space-y-1">
                        {history.slice(0, 6).map((row) => {
                            const level = row.exit_reason === 'stopped_out' ? row.stop_loss : row.target
                            const slip = (row.exit_price ?? 0) - (level ?? 0)
                            // Anything more than a tick past the level was not a
                            // fill at the level — say so.
                            const gapped = Math.abs(slip) > (level ?? 1) * 0.002
                            return (
                                <div key={row.id} className="flex flex-wrap items-baseline gap-x-2 px-1">
                                    <span className="type-caption vibrant w-20">{row.symbol}</span>
                                    <span className={`type-caption ${EXIT_TONE[row.exit_reason] || ''}`}>
                                        {String(row.exit_reason).replace('_', ' ')}
                                    </span>
                                    <span className="type-caption vibrant-tertiary type-numeric">
                                        ₹{Number(row.entry_price).toFixed(0)} → ₹{Number(row.exit_price ?? 0).toFixed(0)}
                                    </span>
                                    {gapped && (
                                        <span
                                            className="type-caption text-alert-300"
                                            title={`Filled ${slip > 0 ? 'past' : 'through'} the level by ₹${Math.abs(slip).toFixed(2)} — the level was not available`}
                                        >
                                            <Zap size={9} className="inline" /> gapped
                                        </span>
                                    )}
                                    <span
                                        className={`type-caption type-numeric ml-auto ${
                                            (row.realised_pnl ?? 0) >= 0 ? 'text-success-300' : 'text-danger-300'
                                        }`}
                                    >
                                        ₹{Number(row.realised_pnl ?? 0).toLocaleString('en-IN')}
                                    </span>
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}
        </section>
    )
}

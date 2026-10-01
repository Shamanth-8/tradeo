import { useCallback, useEffect, useState } from 'react'
import { Brain, Play, Power, RefreshCw, AlertTriangle } from 'lucide-react'
import { flyBrainApi } from '../services/api'
import AnimatedValue from './hud/AnimatedValue'

/**
 * The fly brain's live desk: watchtower hands it openings, it approves or
 * vetoes each, trades the approved ones on the automated paper account, and
 * learns from every close. Polls, so a sweep during market hours shows up
 * without a refresh.
 */

const POLL_MS = 10000

const money = (v) =>
    v == null ? '–' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
const signed = (v) =>
    v == null ? '–' : `${v >= 0 ? '+' : '−'}₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
const tone = (v) => (v > 0 ? 'text-success-400' : v < 0 ? 'text-danger-400' : 'text-white')

const EVENT_TONE = {
    buy: 'text-success-400',
    learned: 'text-primary-400',
    sweep: 'text-dark-300',
    toggle: 'text-alert-400',
    cancelled: 'text-dark-400',
    scan: 'text-dark-400',
}

export default function FlyBrainPanel() {
    const [data, setData] = useState(null)
    const [error, setError] = useState(null)
    const [busy, setBusy] = useState(false)
    const [note, setNote] = useState(null)

    const load = useCallback(async () => {
        try {
            const { data } = await flyBrainApi.dashboard()
            setData(data)
            setError(null)
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || 'Could not reach the fly brain')
        }
    }, [])

    useEffect(() => {
        load()
        const id = setInterval(load, POLL_MS)
        return () => clearInterval(id)
    }, [load])

    const toggle = async () => {
        setBusy(true)
        try {
            await flyBrainApi.setEnabled(!data?.status?.enabled)
            await load()
        } finally {
            setBusy(false)
        }
    }

    const scan = async () => {
        setBusy(true)
        try {
            const { data: res } = await flyBrainApi.scan()
            setNote(res.started
                ? 'Watchtower sweep started — takes a few minutes; decisions appear below.'
                : res.error)
            await load()
        } finally {
            setBusy(false)
        }
    }

    if (!data) {
        return (
            <div className="glass-card p-6 flex items-center gap-3 text-dark-400">
                <Brain className="w-5 h-5" />
                {error ? <span className="text-danger-400">{error}</span> : 'Loading fly brain…'}
            </div>
        )
    }

    const { status, market, watchtower, open_positions: open, closed_trades: closed, events, last_handoff: handoff } = data
    const board = status.scoreboard || {}
    const on = status.enabled

    return (
        <div className="glass-card p-6 space-y-5">
            {/* Header */}
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <Brain className="w-6 h-6 text-primary-400" />
                    <div>
                        <h3 className="text-lg font-semibold text-white">Fly Brain Autotrader</h3>
                        <p className="text-xs text-dark-400">
                            One of the agents trading the automated account above: it judges Watchtower&apos;s openings, trades the ones it approves, and learns from every close. Its vetoes don&apos;t touch other agents&apos; positions.
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <span className={`badge ${market.open ? 'badge-success' : 'badge-alert'}`}>
                        NSE {market.phase} · {market.now_ist} IST
                    </span>
                    <span className={`badge ${watchtower.sweeping ? 'badge-primary' : 'badge-muted'}`}>
                        Watchtower {watchtower.sweeping ? 'sweeping…' : watchtower.running ? 'on' : 'off'}
                    </span>
                    <button onClick={scan} disabled={busy || !on} className="btn-secondary flex items-center gap-2"
                        title="Run a watchtower sweep now">
                        {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                        Scan now
                    </button>
                    <button onClick={toggle} disabled={busy}
                        className={`${on ? 'btn-success' : 'btn-secondary'} flex items-center gap-2`}>
                        <Power className="w-4 h-4" />
                        {on ? 'Trading ON' : 'Trading OFF'}
                    </button>
                </div>
            </div>

            {note && <p className="text-sm text-dark-300">{note}</p>}
            {error && <p className="text-sm text-danger-400">{error}</p>}

            {/* Scoreboard */}
            <div className="stagger grid grid-cols-2 md:grid-cols-6 gap-3">
                <Stat label="Trades closed" value={board.trades ?? 0} />
                <Stat label="Won" value={board.wins ?? 0} className="text-success-400" />
                <Stat label="Lost" value={board.losses ?? 0} className="text-danger-400" />
                <Stat label="Win rate" value={board.win_rate == null ? '–' : `${board.win_rate}%`} />
                <Stat label="Realised P&L" value={signed(board.realised_pnl)} className={tone(board.realised_pnl)} />
                <Stat label={`Open (max ${status.rules?.slots})`} value={board.open ?? 0} />
            </div>

            <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-dark-400">
                <span>
                    🧠 Learned live: <b className="text-success-400">{status.live_rewards} rewards</b> ·{' '}
                    <b className="text-danger-400">{status.live_punishments} punishments</b>
                    {' '}(plus {status.backtest_rewards + status.backtest_punishments} from history)
                </span>
                <span>Approves an opening if it ranks in the top {100 - status.rules?.approve_percentile}% · explores {Math.round(status.rules?.explore * 100)}% of vetoes · {status.rules?.position_pct}% of equity each</span>
            </div>

            {status.backtest && status.backtest.passed === false && (
                <div className="flex items-start gap-2 text-xs text-alert-400 bg-alert-500/10 rounded p-2">
                    <AlertTriangle className="w-4 h-4 shrink-0" />
                    Backtest 2020–26: fly brain {status.backtest.fly_rl_net_annualised_pct}%/yr after costs vs scorer{' '}
                    {status.backtest.scorer_net_annualised_pct}%/yr. It has not shown an edge yet — this paper run is the live test.
                </div>
            )}

            {/* Last handoff */}
            {handoff && (
                <div>
                    <h4 className="text-sm font-semibold text-white mb-2">
                        Last watchtower handoff <span className="text-dark-400 font-normal">at {handoff.at} · {handoff.openings} openings</span>
                    </h4>
                    {handoff.note && <p className="text-sm text-dark-400">{handoff.note}</p>}
                    <div className="space-y-1">
                        {(handoff.decisions || []).map((d) => (
                            <div key={d.symbol} className="flex flex-wrap items-center gap-3 text-sm p-2 bg-dark-700/30 rounded">
                                <span className={`badge ${d.trade ? 'badge-success' : d.decision === 'veto' ? 'badge-danger' : 'badge-muted'}`}>
                                    {d.trade ? (d.decision === 'explore' ? 'BOUGHT (exploring)' : 'BOUGHT') : d.decision.toUpperCase()}
                                </span>
                                <span className="font-semibold text-white">{d.symbol}</span>
                                <span className="text-dark-400">watchtower {d.watchtower_score} · {d.conviction}%</span>
                                {d.fly_percentile != null && (
                                    <span className="text-dark-400">fly {d.fly_percentile}th pct</span>
                                )}
                                {d.via === 'scanner-only' && (
                                    <span className="badge badge-muted" title="Bullish on the scanner but below watchtower's deep-analysis bar; no model reviewed it">scanner-only</span>
                                )}
                                {d.why && <span className="text-dark-500">{d.why}</span>}
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Open positions */}
            <div>
                <h4 className="text-sm font-semibold text-white mb-2">Open fly-brain positions</h4>
                {open.length === 0 ? (
                    <p className="text-sm text-dark-400">None open.</p>
                ) : (
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-dark-700 text-dark-400">
                                <th className="text-left p-2">Stock</th>
                                <th className="text-right p-2">Qty</th>
                                <th className="text-right p-2">Entry</th>
                                <th className="text-right p-2">Now</th>
                                <th className="text-right p-2">Stop / Target</th>
                                <th className="text-right p-2">P&L</th>
                            </tr>
                        </thead>
                        <tbody>
                            {open.map((t) => (
                                <tr key={t.id} className="border-b border-dark-700/50">
                                    <td className="p-2 font-semibold text-white">{t.symbol}</td>
                                    <td className="p-2 text-right text-white">{t.quantity}</td>
                                    <td className="p-2 text-right text-white">{money(t.entry_price)}</td>
                                    <td className="p-2 text-right text-white">{money(t.ltp)}</td>
                                    <td className="p-2 text-right text-dark-300">{money(t.stop_loss)} / {money(t.target)}</td>
                                    <td className={`p-2 text-right ${tone(t.unrealised_pnl)}`}>
                                        {signed(t.unrealised_pnl)} {t.return_pct != null && `(${t.return_pct}%)`}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            <div className="grid md:grid-cols-2 gap-5">
                {/* Closed trades */}
                <div>
                    <h4 className="text-sm font-semibold text-white mb-2">Closed trades</h4>
                    {closed.length === 0 ? (
                        <p className="text-sm text-dark-400">No closed trades yet.</p>
                    ) : (
                        <div className="space-y-1 max-h-72 overflow-y-auto">
                            {closed.map((t) => (
                                <div key={t.id} className="flex items-center justify-between text-sm p-2 bg-dark-700/30 rounded">
                                    <span>
                                        <span className={t.realised_pnl > 0 ? 'text-success-400' : 'text-danger-400'}>
                                            {t.state === 'cancelled' ? '⊘' : t.realised_pnl > 0 ? '✅' : '❌'}
                                        </span>{' '}
                                        <b className="text-white">{t.symbol}</b>{' '}
                                        <span className="text-dark-400">{(t.exit_reason || t.state).replace('_', ' ')}</span>
                                    </span>
                                    <span className={tone(t.realised_pnl)}>{signed(t.realised_pnl)}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Activity */}
                <div>
                    <h4 className="text-sm font-semibold text-white mb-2">Activity</h4>
                    {events.length === 0 ? (
                        <p className="text-sm text-dark-400">
                            Nothing yet. During market hours watchtower sweeps every 30 minutes and hands its openings here.
                        </p>
                    ) : (
                        <div className="space-y-1 max-h-72 overflow-y-auto font-mono text-xs">
                            {events.map((e, i) => (
                                <div key={i} className={EVENT_TONE[e.kind] || 'text-dark-300'}>
                                    <span className="text-dark-500">{e.at.slice(5, 16)}</span> {e.text}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}

function Stat({ label, value, className = 'text-white' }) {
    return (
        <div className="stat-card">
            <p className="text-xs text-dark-400">{label}</p>
            <p className={`text-xl font-bold ${className}`}><AnimatedValue value={value} /></p>
        </div>
    )
}

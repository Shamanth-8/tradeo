import { useCallback, useEffect, useMemo, useState } from 'react'
import {
    Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart,
    ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, Area,
} from 'recharts'
import { FlaskConical, Play, RefreshCw, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'
import { flyBrainApi } from '../services/api'
import AnimatedValue from './hud/AnimatedValue'

/**
 * History lab: what the fly brain did with 2018→today, and what it thinks of
 * today's market. Loaded lazily from the Paper Trading page because recharts
 * is heavy.
 */

const AXIS = { stroke: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }
const TOOLTIP = {
    contentStyle: {
        background: 'rgba(10,17,32,0.96)', border: '1px solid rgba(34,211,238,0.25)',
        borderRadius: 8, fontSize: 11, fontFamily: 'JetBrains Mono',
    },
    labelStyle: { color: '#67e8f9' },
}
// One colour per contender, the same on every chart.
const COLORS = {
    fly_brain: '#a78bfa', scorer: '#38bdf8', random: '#94a3b8', buy_everything: '#f59e0b',
    rewards: '#22c55e', punishments: '#ef4444',
}
const NAMES = {
    fly_brain: 'Fly brain (RL)', scorer: 'Tradeo scorer', random: 'Random 5 stocks',
    buy_everything: 'Buy everything', random_wiring: 'Random wiring + RL',
    no_brain: 'RL without brain', random_picks: 'Random 5 stocks',
}
const CHECK_LABELS = {
    profitable_after_costs: 'Profitable after costs',
    beats_scorer: 'Beats Tradeo scorer',
    beats_random_wiring: 'Beats random wiring (the fly wiring matters)',
    beats_no_network: 'Beats RL without a brain',
    beats_buying_everything: 'Beats buying everything',
    positive_on_every_seed: 'Positive on every seed',
}
const POLL_MS = 5000

const pct = (v, d = 1) => (v == null ? '–' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`)
const tone = (v) => (v > 0 ? 'text-success-400' : v < 0 ? 'text-danger-400' : 'text-white')

export default function FlyHistoryLab() {
    const [history, setHistory] = useState(null)
    const [runState, setRunState] = useState(null)
    const [today, setToday] = useState(null)
    const [todayError, setTodayError] = useState(null)
    const [live, setLive] = useState([])

    const loadHistory = useCallback(async () => {
        const { data } = await flyBrainApi.history()
        setHistory(data.result)
        setRunState(data.status)
        return data.status
    }, [])

    useEffect(() => {
        loadHistory().catch(() => {})
        flyBrainApi.today()
            .then(({ data }) => setToday(data))
            .catch((err) => setTodayError(err?.response?.data?.detail || err.message))
        flyBrainApi.dashboard()
            .then(({ data }) => setLive(data.closed_trades || []))
            .catch(() => {})
    }, [loadHistory])

    // Poll while a replay is running, then pick up the new result.
    useEffect(() => {
        if (!runState?.running) return
        const id = setInterval(() => loadHistory().catch(() => {}), POLL_MS)
        return () => clearInterval(id)
    }, [runState?.running, loadHistory])

    const run = async () => {
        const { data } = await flyBrainApi.runHistory()
        if (data.started) setRunState({ running: true })
    }

    // Live paper: cumulative realised P&L as fly trades close.
    const liveCurve = useMemo(() => {
        let total = 0
        return [...live]
            .filter((t) => t.state !== 'cancelled')
            .sort((a, b) => String(a.closed_at).localeCompare(String(b.closed_at)))
            .map((t) => {
                total += Number(t.realised_pnl || 0)
                return { date: String(t.closed_at).slice(5, 16), symbol: t.symbol, pnl: Math.round(total) }
            })
    }, [live])

    const running = runState?.running
    const s = history?.summary

    return (
        <div className="glass-card p-6 space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <FlaskConical className="w-6 h-6 text-primary-400" />
                    <div>
                        <h3 className="text-lg font-semibold text-white">Fly Brain · History Lab</h3>
                        <p className="text-xs text-dark-400">
                            {history
                                ? `Replayed ${history.data.from} → ${history.data.to} on ${history.data.symbols} NSE stocks, rebalancing every ${history.data.rebalance_days} days, ${history.data.round_trip_cost_pct}% costs per round trip. Last run ${history.generated_at.replace('T', ' ')}.`
                                : 'Replay the fly brain on historical NSE data, train it, and see how it did.'}
                        </p>
                    </div>
                </div>
                <button onClick={run} disabled={running} className="btn-primary flex items-center gap-2">
                    {running ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                    {running ? 'Replaying… (~1 min)' : 'Replay & train on history'}
                </button>
            </div>

            {runState?.error && <p className="text-sm text-danger-400">Last run failed: {runState.error}</p>}
            {!history && !running && (
                <p className="text-sm text-dark-400">No replay yet — press the button.</p>
            )}

            {s && (
                <>
                    {/* Headline numbers */}
                    <div className="stagger grid grid-cols-2 md:grid-cols-4 gap-3">
                        {['fly_brain', 'scorer', 'random_picks', 'buy_everything'].map((k) => (
                            <div key={k} className="stat-card">
                                <p className="text-xs text-dark-400">{NAMES[k]} · per year</p>
                                <p className={`text-xl font-bold ${tone(s.net_per_year_pct[k])}`}>
                                    {pct(s.net_per_year_pct[k], 2)}
                                </p>
                            </div>
                        ))}
                    </div>
                    <div className="stagger grid grid-cols-2 md:grid-cols-4 gap-3">
                        <Mini label="Historic trades" value={s.trades.toLocaleString()} />
                        <Mini label="Won (rewards)" value={s.wins.toLocaleString()} className="text-success-400" />
                        <Mini label="Lost (punishments)" value={s.losses.toLocaleString()} className="text-danger-400" />
                        <Mini label="Win rate" value={`${s.win_rate}%`} />
                    </div>
                    <p className="text-xs text-dark-400">
                        Net returns are for the test years {s.test_years} after charges and slippage, averaged over
                        5 random seeds (fly brain by seed: {s.net_per_year_pct.fly_brain_by_seed.map((v) => pct(v)).join(', ')}).
                        {s.live_trades_reapplied > 0 && ` ${s.live_trades_reapplied} live paper trades were re-applied after retraining.`}
                    </p>

                    {/* Gate */}
                    <div className={`rounded-lg p-3 border ${s.passed ? 'border-success-400/30 bg-success-500/5' : 'border-alert-400/30 bg-alert-500/5'}`}>
                        <div className="flex items-center gap-2 mb-2">
                            <AlertTriangle className={`w-4 h-4 ${s.passed ? 'text-success-400' : 'text-alert-400'}`} />
                            <span className="text-sm font-semibold text-white">
                                {s.passed ? 'Passed every check' : 'Has not shown an edge on history'}
                            </span>
                        </div>
                        <div className="grid md:grid-cols-3 gap-1 text-xs">
                            {Object.entries(s.checks).map(([k, ok]) => (
                                <span key={k} className="flex items-center gap-1.5">
                                    {ok ? <CheckCircle2 className="w-3.5 h-3.5 text-success-400" /> : <XCircle className="w-3.5 h-3.5 text-danger-400" />}
                                    <span className={ok ? 'text-dark-200' : 'text-dark-400'}>{CHECK_LABELS[k] || k}</span>
                                </span>
                            ))}
                        </div>
                    </div>

                    {/* Equity */}
                    <Chart title="Growth of ₹1 (after costs)" note="Every strategy trades every 5 days; the fly brain sits in cash when it expects a loss.">
                        <LineChart data={history.equity}>
                            <CartesianGrid stroke="#1e293b" vertical={false} />
                            <XAxis dataKey="date" {...AXIS} minTickGap={50} />
                            <YAxis {...AXIS} domain={['auto', 'auto']} tickFormatter={(v) => `₹${v.toFixed(2)}`} />
                            <Tooltip {...TOOLTIP} formatter={(v, n) => [`₹${Number(v).toFixed(3)}`, NAMES[n] || n]} />
                            <Legend formatter={(n) => NAMES[n] || n} wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine y={1} stroke="#475569" strokeDasharray="4 4" />
                            {['fly_brain', 'scorer', 'random', 'buy_everything'].map((k) => (
                                <Line key={k} dataKey={k} stroke={COLORS[k]} dot={false}
                                    strokeWidth={k === 'fly_brain' ? 2.2 : 1.3} isAnimationActive={false} />
                            ))}
                        </LineChart>
                    </Chart>

                    <div className="grid lg:grid-cols-2 gap-5">
                        {/* Learning */}
                        <Chart title="How it learned: rewards vs punishments" note={`Right axis: win rate over its last 100 trades.`}>
                            <ComposedChart data={history.learning}>
                                <CartesianGrid stroke="#1e293b" vertical={false} />
                                <XAxis dataKey="date" {...AXIS} minTickGap={50} />
                                <YAxis yAxisId="n" {...AXIS} />
                                <YAxis yAxisId="w" orientation="right" {...AXIS} domain={[20, 80]} tickFormatter={(v) => `${v}%`} />
                                <Tooltip {...TOOLTIP} />
                                <Legend wrapperStyle={{ fontSize: 11 }} />
                                <Line yAxisId="n" dataKey="rewards" name="Rewards (wins)" stroke={COLORS.rewards} dot={false} isAnimationActive={false} />
                                <Line yAxisId="n" dataKey="punishments" name="Punishments (losses)" stroke={COLORS.punishments} dot={false} isAnimationActive={false} />
                                <Line yAxisId="w" dataKey="win_rate" name="Win rate %" stroke="#e2e8f0" strokeDasharray="3 3" dot={false} isAnimationActive={false} />
                                <ReferenceLine yAxisId="w" y={50} stroke="#475569" strokeDasharray="2 4" />
                            </ComposedChart>
                        </Chart>

                        {/* Caution */}
                        <Chart title="What it expected, and how much it invested" note="Left: its predicted net return of the trades it took. Right: share of the 5 slots it filled (the rest sat in cash).">
                            <ComposedChart data={history.learning}>
                                <CartesianGrid stroke="#1e293b" vertical={false} />
                                <XAxis dataKey="date" {...AXIS} minTickGap={50} />
                                <YAxis yAxisId="e" {...AXIS} tickFormatter={(v) => `${v}%`} />
                                <YAxis yAxisId="i" orientation="right" {...AXIS} domain={[0, 100]} tickFormatter={(v) => `${v}%`} />
                                <Tooltip {...TOOLTIP} />
                                <Legend wrapperStyle={{ fontSize: 11 }} />
                                <Area yAxisId="i" dataKey="invested_pct" name="Invested %" fill="#38bdf8" fillOpacity={0.12} stroke="#38bdf8" strokeOpacity={0.4} isAnimationActive={false} />
                                <Line yAxisId="e" dataKey="expected_pct" name="Expected net %" stroke={COLORS.fly_brain} dot={false} isAnimationActive={false} />
                                <ReferenceLine yAxisId="e" y={0} stroke="#475569" strokeDasharray="4 4" />
                            </ComposedChart>
                        </Chart>
                    </div>

                    {/* Years */}
                    <Chart title="Net return by year" height={240}>
                        <BarChart data={history.by_year}>
                            <CartesianGrid stroke="#1e293b" vertical={false} />
                            <XAxis dataKey="year" {...AXIS} />
                            <YAxis {...AXIS} tickFormatter={(v) => `${v}%`} />
                            <Tooltip {...TOOLTIP} formatter={(v, n) => [pct(v), NAMES[n] || n]} />
                            <Legend formatter={(n) => NAMES[n] || n} wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine y={0} stroke="#475569" />
                            {['fly_brain', 'scorer', 'buy_everything'].map((k) => (
                                <Bar key={k} dataKey={k} fill={COLORS[k]} isAnimationActive={false} />
                            ))}
                        </BarChart>
                    </Chart>
                </>
            )}

            {/* Today */}
            <Chart title="Today: how the fly brain values each stock"
                note={today
                    ? `Predicted net return over ~5 days from data to ${today.as_of}. Green = top half, the ones it would approve if watchtower passed them.`
                    : 'Computing from live prices (about 20 seconds)…'}
                height={260}>
                {today ? (
                    <BarChart data={today.all}>
                        <CartesianGrid stroke="#1e293b" vertical={false} />
                        <XAxis dataKey="symbol" {...AXIS} interval={0} angle={-60} textAnchor="end" height={60} fontSize={8} />
                        <YAxis {...AXIS} tickFormatter={(v) => `${v}%`} />
                        <Tooltip {...TOOLTIP} formatter={(v, n, p) => [`${v}% · ${p.payload.percentile}th pct`, 'Fly value']} />
                        <ReferenceLine y={0} stroke="#475569" />
                        <Bar dataKey="expected_net_return_pct" isAnimationActive={false}>
                            {today.all.map((r) => (
                                <Cell key={r.symbol} fill={r.tradeable ? COLORS.rewards : '#475569'} />
                            ))}
                        </Bar>
                    </BarChart>
                ) : (
                    <div className="h-full flex items-center justify-center text-sm text-dark-400">
                        {todayError ? <span className="text-danger-400">{todayError}</span> : <RefreshCw className="w-5 h-5 animate-spin" />}
                    </div>
                )}
            </Chart>

            {/* Live */}
            <Chart title="Live paper record: cumulative P&L" height={200}
                note={liveCurve.length ? `${liveCurve.length} closed fly-brain paper trades.` : 'No closed fly-brain paper trades yet — this fills in as live trades close.'}>
                {liveCurve.length ? (
                    <LineChart data={liveCurve}>
                        <CartesianGrid stroke="#1e293b" vertical={false} />
                        <XAxis dataKey="date" {...AXIS} />
                        <YAxis {...AXIS} tickFormatter={(v) => `₹${v}`} />
                        <Tooltip {...TOOLTIP} formatter={(v, n, p) => [`₹${v} (after ${p.payload.symbol})`, 'P&L']} />
                        <ReferenceLine y={0} stroke="#475569" />
                        <Line dataKey="pnl" stroke={COLORS.fly_brain} isAnimationActive={false} />
                    </LineChart>
                ) : (
                    <div className="h-full flex items-center justify-center text-sm text-dark-500">—</div>
                )}
            </Chart>

            {history && (
                <div className="grid md:grid-cols-2 gap-5">
                    <Table title="Latest historic trades"
                        head={['Date', 'Stock', 'Expected', 'Result']}
                        rows={history.recent_trades.slice(0, 15).map((t) => [
                            t.date, t.symbol, pct(t.expected_pct, 2),
                            <span key="r" className={tone(t.net_pct)}>{pct(t.net_pct, 2)}</span>,
                        ])} />
                    <Table title="Stocks it traded most"
                        head={['Stock', 'Trades', 'Avg net', 'Win rate']}
                        rows={history.by_symbol.slice(0, 15).map((r) => [
                            r.symbol, r.trades,
                            <span key="a" className={tone(r.avg_pct)}>{pct(r.avg_pct, 2)}</span>,
                            `${r.win_rate}%`,
                        ])} />
                </div>
            )}
        </div>
    )
}

function Chart({ title, note, height = 280, children }) {
    return (
        <div>
            <h4 className="text-sm font-semibold text-white">{title}</h4>
            {note && <p className="text-xs text-dark-400 mb-2">{note}</p>}
            <div style={{ height }}>
                <ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer>
            </div>
        </div>
    )
}

function Mini({ label, value, className = 'text-white' }) {
    return (
        <div className="stat-card">
            <p className="text-xs text-dark-400">{label}</p>
            <p className={`text-lg font-bold ${className}`}><AnimatedValue value={value} /></p>
        </div>
    )
}

function Table({ title, head, rows }) {
    return (
        <div>
            <h4 className="text-sm font-semibold text-white mb-2">{title}</h4>
            <table className="w-full text-xs">
                <thead>
                    <tr className="border-b border-dark-700 text-dark-400">
                        {head.map((h, i) => <th key={h} className={`p-1.5 ${i ? 'text-right' : 'text-left'}`}>{h}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((cells, r) => (
                        <tr key={r} className="border-b border-dark-700/40">
                            {cells.map((c, i) => <td key={i} className={`p-1.5 ${i ? 'text-right' : 'text-left text-white'}`}>{c}</td>)}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}

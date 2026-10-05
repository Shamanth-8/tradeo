import { useCallback, useEffect, useState } from 'react'
import {
    AlertTriangle, Bot, Check, Play, RefreshCw, Shield, ShieldAlert, X,
} from 'lucide-react'
import { HudPanel, Stat, Meter, Delta, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import TradingAgents from '../components/TradingAgents'
import RiskPanel from '../components/RiskPanel'
import EvaluationPanel from '../components/EvaluationPanel'
import TriggerPanel from '../components/TriggerPanel'
import { autopilotApi } from '../services/api'

/**
 * Autopilot — what the agent wants to do, and why it was or wasn't allowed to.
 *
 * The safety banner is the most important element on the page: anyone leaving
 * an agent running should be able to tell at a glance whether real money is
 * reachable.
 */

const inr = (n, d = 0) =>
    `₹${Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: d })}`

const STATUS_STYLE = {
    executed: 'badge-success',
    approved: 'badge-primary',
    awaiting_approval: 'badge-alert',
    blocked: 'badge-danger',
    rejected: 'badge-muted',
    failed: 'badge-danger',
}

export default function Autopilot() {
    const [safety, setSafety] = useState(null)
    const [status, setStatus] = useState(null)
    const [proposals, setProposals] = useState([])
    const [account, setAccount] = useState(null)
    const [performance, setPerformance] = useState(null)
    const [loading, setLoading] = useState(true)
    const [running, setRunning] = useState(false)
    const [error, setError] = useState(null)

    const load = useCallback(async () => {
        try {
            const [s, st, p, a, perf] = await Promise.allSettled([
                autopilotApi.safety(),
                autopilotApi.status(),
                autopilotApi.proposals(null, 25),
                autopilotApi.account(),
                autopilotApi.performance(),
            ])
            if (s.status === 'fulfilled') setSafety(s.value.data)
            if (st.status === 'fulfilled') setStatus(st.value.data)
            if (p.status === 'fulfilled') setProposals(p.value.data.proposals || [])
            if (a.status === 'fulfilled') setAccount(a.value.data)
            if (perf.status === 'fulfilled') setPerformance(perf.value.data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        load()
    }, [load])

    const runCycle = async () => {
        setRunning(true)
        setError(null)
        try {
            await autopilotApi.run()
            await load()
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        } finally {
            setRunning(false)
        }
    }

    const decide = async (id, approve) => {
        try {
            await (approve ? autopilotApi.approve(id) : autopilotApi.reject(id))
            await load()
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        }
    }

    // The switches never wait for the slower sections below (positions are
    // priced live, which can take seconds on a cold start).
    if (loading) {
        return (
            <div className="space-y-5">
                <TradingAgents />
                <Loading rows={3} label="Pricing open positions" />
            </div>
        )
    }

    const live = safety?.can_touch_real_money

    return (
        <div className="space-y-5">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="font-mono text-lg uppercase tracking-[0.3em] text-primary-300 text-glow">
                        Autopilot
                    </h1>
                    <p className="mt-1 text-xs text-dark-400">
                        Switch the trading agents on or off and set their rules. Their trades and results are on the Paper Trading page.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button onClick={load} className="btn-ghost">
                        <RefreshCw className="h-3.5 w-3.5" /> Refresh
                    </button>
                    <button onClick={runCycle} disabled={running || !safety?.enabled} className="btn-primary">
                        <Play className={`h-3.5 w-3.5 ${running ? 'animate-pulse' : ''}`} />
                        {running ? 'Thinking…' : 'Run cycle'}
                    </button>
                </div>
            </header>

            <RiskPanel />

            <TradingAgents />

            <EvaluationPanel />

            {/* The agents' open brackets, live. */}
            <TriggerPanel />

            <div className="border-t border-dark-800/60 pt-4">
                <h2 className="font-mono text-xs uppercase tracking-[0.25em] text-dark-400">Proposal agent (older, switched off)</h2>
                <p className="mt-1 text-[11px] text-dark-500">
                    Makes trade proposals you approve one by one; it doesn't trade on its own. Listed for removal —
                    kept only until you decide.
                </p>
            </div>

            <ErrorNote error={error} onRetry={load} />

            {/* ---- Safety banner ---- */}
            <div
                className={`hud-corners rounded-lg border p-4 ${
                    live
                        ? 'border-danger-400/50 bg-danger-500/10 shadow-glow-danger'
                        : 'border-primary-400/25 bg-primary-500/5'
                }`}
            >
                <div className="flex items-start gap-3">
                    {live ? (
                        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-danger-400" />
                    ) : (
                        <Shield className="mt-0.5 h-5 w-5 shrink-0 text-primary-400" />
                    )}
                    <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                            <span
                                className={`font-mono text-sm uppercase tracking-[0.2em] ${
                                    live ? 'text-danger-300' : 'text-primary-300'
                                }`}
                            >
                                {live ? 'Live — real money' : `Mode: ${safety?.mode}`}
                            </span>
                            <span className={safety?.enabled ? 'badge-success' : 'badge-muted'}>
                                {safety?.enabled ? 'enabled' : 'disabled'}
                            </span>
                        </div>
                        <p className="mt-1 text-xs leading-relaxed text-dark-300">
                            {safety?.explanation}
                        </p>
                        <div className="mt-2.5 flex flex-wrap gap-1.5">
                            {Object.entries(safety?.switches || {}).map(([key, value]) => (
                                <span
                                    key={key}
                                    className={
                                        value === true
                                            ? 'badge-alert'
                                            : value === false
                                              ? 'badge-muted'
                                              : 'badge-primary'
                                    }
                                >
                                    {key}={String(value)}
                                </span>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            {/* ---- Paper account ---- */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <HudPanel corners={false} glow>
                    <Stat label="Paper equity" value={inr(account?.equity)} />
                </HudPanel>
                <HudPanel corners={false}>
                    <Stat
                        label="Return"
                        value={inr(account?.total_return)}
                        tone={account?.total_return >= 0 ? 'up' : 'down'}
                        sub={`${account?.total_return_percent >= 0 ? '+' : ''}${account?.total_return_percent}%`}
                    />
                </HudPanel>
                <HudPanel corners={false}>
                    <Stat
                        label="Win rate"
                        value={performance?.win_rate !== null ? `${performance?.win_rate}%` : '—'}
                        sub={`${performance?.closed_trades || 0} closed`}
                        tone="primary"
                    />
                </HudPanel>
                <HudPanel corners={false}>
                    <Stat
                        label="Trades today"
                        value={`${status?.trades_today || 0}/${safety?.limits?.max_daily_trades}`}
                        sub={`cap ${safety?.limits?.max_position_percent}% per position`}
                    />
                </HudPanel>
            </div>

            <div className="grid gap-5 lg:grid-cols-[1fr_300px]">
                {/* ---- Proposals ---- */}
                <HudPanel
                    title="Proposals"
                    subtitle="Every decision, with the full guardrail trace"
                    padded={false}
                >
                    {proposals.length === 0 ? (
                        <Empty
                            icon={Bot}
                            title="No proposals yet"
                            hint={
                                safety?.enabled
                                    ? 'Run a cycle to have the agent evaluate recent opportunities.'
                                    : 'Autopilot is disabled. Set AUTOPILOT_ENABLED=true in backend/.env.'
                            }
                        />
                    ) : (
                        <div className="divide-y divide-dark-800/60">
                            {proposals.map((p) => (
                                <Proposal key={p.id} proposal={p} onDecide={decide} />
                            ))}
                        </div>
                    )}
                </HudPanel>

                <div className="space-y-5">
                    <HudPanel title="Open positions" corners={false}>
                        {account?.positions?.length > 0 ? (
                            <div className="space-y-2">
                                {account.positions.map((pos) => (
                                    <div
                                        key={pos.symbol}
                                        className="rounded border border-dark-700/60 px-3 py-2"
                                    >
                                        <div className="flex items-center justify-between">
                                            <span className="font-mono text-xs text-dark-100">
                                                {pos.symbol}
                                            </span>
                                            <Delta value={pos.pnl_percent} className="text-[11px]" />
                                        </div>
                                        <div className="mt-0.5 font-mono text-[10px] text-dark-500">
                                            {pos.quantity} @ {inr(pos.avg_price, 2)} → {inr(pos.ltp, 2)}
                                            {pos.stop_loss && ` · stop ${inr(pos.stop_loss, 2)}`}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="py-3 text-center text-xs text-dark-500">
                                No open paper positions.
                            </p>
                        )}
                    </HudPanel>

                    <HudPanel title="Guardrails" corners={false}>
                        <ul className="space-y-1">
                            {(safety?.guardrails || []).map((g) => (
                                <li
                                    key={g}
                                    className="flex items-start gap-2 text-[11px] leading-snug text-dark-400"
                                >
                                    <Check className="mt-0.5 h-3 w-3 shrink-0 text-success-400/70" />
                                    {g}
                                </li>
                            ))}
                        </ul>
                    </HudPanel>
                </div>
            </div>
        </div>
    )
}

function Proposal({ proposal: p, onDecide }) {
    const [open, setOpen] = useState(false)
    const guard = p.guardrails || {}
    const blockers = guard.blockers || []

    return (
        <div className="transition-colors hover:bg-primary-500/5">
            <button
                onClick={() => setOpen((v) => !v)}
                className="flex w-full items-center gap-4 px-4 py-3 text-left"
            >
                <span
                    className={`w-12 shrink-0 font-mono text-[10px] uppercase ${
                        p.side === 'BUY' ? 'text-success-400' : 'text-danger-400'
                    }`}
                >
                    {p.side}
                </span>

                <div className="w-32 shrink-0">
                    <div className="font-mono text-sm text-dark-100">{p.symbol}</div>
                    <div className="font-mono text-[10px] text-dark-600">
                        {p.quantity} @ {inr(p.price, 2)}
                    </div>
                </div>

                <span className={STATUS_STYLE[p.status] || 'badge-muted'}>
                    {p.status?.replace(/_/g, ' ')}
                </span>

                <div className="hidden w-20 shrink-0 md:block">
                    <Meter value={p.conviction || 0} tone="primary" />
                    <div className="mt-1 font-mono text-[10px] text-dark-500">{p.conviction}%</div>
                </div>

                <p className="hidden min-w-0 flex-1 truncate text-xs text-dark-500 lg:block">
                    {blockers.length > 0 ? `Blocked: ${blockers.join(', ')}` : p.thesis}
                </p>

                <span className="shrink-0 font-mono text-xs tabular-nums text-dark-300">
                    {inr(p.value)}
                </span>
            </button>

            {open && (
                <div className="animate-fade-in space-y-4 border-t border-dark-800/60 bg-dark-950/40 px-4 py-4">
                    {p.thesis && <p className="text-sm leading-relaxed text-dark-200">{p.thesis}</p>}

                    <div>
                        <div className="hud-label mb-2">Guardrail trace</div>
                        <div className="space-y-1">
                            {(guard.checks || []).map((c) => (
                                <div key={c.name} className="flex items-start gap-2 text-[11px]">
                                    <span
                                        className={`mt-0.5 w-12 shrink-0 font-mono uppercase ${
                                            c.passed
                                                ? 'text-success-400'
                                                : c.blocking
                                                  ? 'text-danger-400'
                                                  : 'text-alert-400'
                                        }`}
                                    >
                                        {c.passed ? 'pass' : c.blocking ? 'block' : 'warn'}
                                    </span>
                                    <span className="w-40 shrink-0 font-mono text-dark-500">
                                        {c.name.replace(/_/g, ' ')}
                                    </span>
                                    <span className="flex-1 text-dark-400">{c.detail}</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    <div className="flex flex-wrap gap-4 border-t border-dark-800/60 pt-3">
                        {p.stop_loss && <Stat label="Stop" value={inr(p.stop_loss, 2)} tone="down" />}
                        {p.targets?.length > 0 && (
                            <Stat label="Targets" value={p.targets.map((t) => inr(t, 2)).join(' · ')} tone="up" />
                        )}
                        <Stat label="Mode" value={p.mode} mono={false} tone="muted" />
                        {p.executed_price && (
                            <Stat label="Filled at" value={inr(p.executed_price, 2)} tone="primary" />
                        )}
                    </div>

                    {p.status === 'awaiting_approval' && (
                        <div className="flex items-center gap-2 border-t border-dark-800/60 pt-3">
                            <button onClick={() => onDecide(p.id, true)} className="btn-primary">
                                <Check className="h-3.5 w-3.5" /> Approve & execute
                            </button>
                            <button onClick={() => onDecide(p.id, false)} className="btn-ghost">
                                <X className="h-3.5 w-3.5" /> Reject
                            </button>
                            <span className="flex items-center gap-1 text-[10px] text-alert-400">
                                <AlertTriangle className="h-3 w-3" /> Guardrails re-run at execution
                            </span>
                        </div>
                    )}

                    {p.result?.error && (
                        <p className="text-xs text-danger-400">{p.result.error}</p>
                    )}
                </div>
            )}
        </div>
    )
}

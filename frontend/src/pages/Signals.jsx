import { useCallback, useEffect, useState } from 'react'
import { Eye, Play, Radar, RefreshCw, Send, Trash2, TrendingUp } from 'lucide-react'
import { HudPanel, Stat, Meter, StatusDot, Delta, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import { signalsApi } from '../services/api'

/**
 * Signals — what the watchtower has found, and control over the sweep.
 */

const VERDICT = {
    strong_buy: { style: 'badge-success', tone: 'success' },
    buy: { style: 'badge-success', tone: 'success' },
    watch: { style: 'badge-alert', tone: 'alert' },
    avoid: { style: 'badge-danger', tone: 'danger' },
    exit: { style: 'badge-danger', tone: 'danger' },
}

export default function Signals() {
    const [opportunities, setOpportunities] = useState([])
    const [status, setStatus] = useState(null)
    const [watchlist, setWatchlist] = useState([])
    const [telegram, setTelegram] = useState(null)
    const [loading, setLoading] = useState(true)
    const [scanning, setScanning] = useState(false)
    const [error, setError] = useState(null)
    const [newSymbol, setNewSymbol] = useState('')

    const load = useCallback(async () => {
        setError(null)
        try {
            const [o, s, w, t] = await Promise.allSettled([
                signalsApi.opportunities({ limit: 30, since_hours: 168 }),
                signalsApi.status(),
                signalsApi.watchlist(),
                signalsApi.telegram(),
            ])
            if (o.status === 'fulfilled') setOpportunities(o.value.data.opportunities || [])
            if (s.status === 'fulfilled') setStatus(s.value.data)
            if (w.status === 'fulfilled') setWatchlist(w.value.data.watchlist || [])
            if (t.status === 'fulfilled') setTelegram(t.value.data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        load()
        const timer = setInterval(load, 45000)
        return () => clearInterval(timer)
    }, [load])

    const runScan = async () => {
        setScanning(true)
        try {
            await signalsApi.scan({ limit: 40, deep: true })
            // A deep scan runs in the background; poll rather than block.
            setTimeout(load, 8000)
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        } finally {
            setTimeout(() => setScanning(false), 8000)
        }
    }

    const addWatch = async () => {
        if (!newSymbol.trim()) return
        await signalsApi.watch(newSymbol.trim())
        setNewSymbol('')
        load()
    }

    if (loading) return <Loading rows={5} label="Reading the watchtower" />

    const market = status?.market

    return (
        <div className="space-y-5">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="font-mono text-lg uppercase tracking-[0.3em] text-primary-300 text-glow">
                        Watchtower
                    </h1>
                    <p className="mt-1 text-xs text-dark-400">
                        Continuous sweep across every asset class, with AI verdicts on what clears.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button onClick={load} className="btn-ghost">
                        <RefreshCw className="h-3.5 w-3.5" /> Refresh
                    </button>
                    <button onClick={runScan} disabled={scanning} className="btn-primary">
                        <Radar className={`h-3.5 w-3.5 ${scanning ? 'animate-spin' : ''}`} />
                        {scanning ? 'Sweeping…' : 'Scan now'}
                    </button>
                </div>
            </header>

            <ErrorNote error={error} onRetry={load} />

            {/* ---- Engine status ---- */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <HudPanel corners={false}>
                    <div className="flex items-center justify-between">
                        <Stat
                            label="Engine"
                            value={status?.running ? 'Running' : 'Stopped'}
                            mono={false}
                            tone={status?.running ? 'up' : 'muted'}
                            sub={`every ${status?.interval_minutes}m`}
                        />
                        <StatusDot status={status?.running ? 'ok' : 'idle'} />
                    </div>
                </HudPanel>
                <HudPanel corners={false}>
                    <Stat
                        label="Market"
                        value={market?.phase?.replace('_', ' ') || '—'}
                        mono={false}
                        tone={market?.is_open ? 'up' : 'muted'}
                        sub={market?.is_open ? `${market.closes_in_minutes}m to close` : market?.session}
                    />
                </HudPanel>
                <HudPanel corners={false}>
                    <Stat
                        label="Alert threshold"
                        value={`${status?.alert_min_conviction}%`}
                        sub="conviction to push"
                    />
                </HudPanel>
                <HudPanel corners={false}>
                    <div className="flex items-center justify-between">
                        <Stat
                            label="Telegram"
                            value={telegram?.valid ? 'Live' : telegram?.configured ? 'Error' : 'Not set'}
                            mono={false}
                            tone={telegram?.valid ? 'up' : 'muted'}
                            sub={telegram?.username ? `@${telegram.username}` : undefined}
                        />
                        <Send className="h-3.5 w-3.5 text-dark-600" />
                    </div>
                </HudPanel>
            </div>

            <div className="grid gap-5 lg:grid-cols-[1fr_300px]">
                {/* ---- Opportunities ---- */}
                <HudPanel
                    title="Opportunities"
                    subtitle="Names that cleared the score gate and earned full analysis"
                    padded={false}
                >
                    {opportunities.length === 0 ? (
                        <Empty
                            icon={TrendingUp}
                            title="Nothing flagged"
                            hint="The scanner sweeps during market hours. Run one now to see what's out there."
                            action={
                                <button onClick={runScan} className="btn-primary mt-2">
                                    <Play className="h-3.5 w-3.5" /> Scan now
                                </button>
                            }
                        />
                    ) : (
                        <div className="divide-y divide-dark-800/60">
                            {opportunities.map((o) => (
                                <Opportunity key={o.id} opportunity={o} />
                            ))}
                        </div>
                    )}
                </HudPanel>

                {/* ---- Watchlist ---- */}
                <div className="space-y-5">
                    <HudPanel title="Watchlist" subtitle="Checked every 15 minutes">
                        <div className="space-y-2">
                            <div className="flex gap-2">
                                <input
                                    value={newSymbol}
                                    onChange={(e) => setNewSymbol(e.target.value)}
                                    onKeyDown={(e) => e.key === 'Enter' && addWatch()}
                                    placeholder="Add symbol…"
                                    className="input-field !py-2 text-xs"
                                />
                                <button onClick={addWatch} className="btn-ghost !px-2.5">
                                    <Eye className="h-3.5 w-3.5" />
                                </button>
                            </div>

                            {watchlist.length === 0 ? (
                                <p className="py-3 text-center text-xs text-dark-500">
                                    Nothing watched yet.
                                </p>
                            ) : (
                                watchlist.map((w) => (
                                    <div
                                        key={w.symbol}
                                        className="flex items-center justify-between rounded border border-dark-700/60 px-3 py-2"
                                    >
                                        <div className="min-w-0">
                                            <div className="font-mono text-xs text-dark-100">{w.symbol}</div>
                                            <div className="truncate text-[10px] text-dark-500">{w.name}</div>
                                        </div>
                                        <button
                                            onClick={async () => {
                                                await signalsApi.unwatch(w.symbol)
                                                load()
                                            }}
                                            className="shrink-0 text-dark-600 hover:text-danger-400"
                                        >
                                            <Trash2 className="h-3.5 w-3.5" />
                                        </button>
                                    </div>
                                ))
                            )}
                        </div>
                    </HudPanel>

                    <HudPanel title="Recent sweeps" corners={false}>
                        <div className="space-y-1.5">
                            {(status?.recent_scans || []).slice(0, 6).map((scan) => (
                                <div
                                    key={scan.id}
                                    className="flex items-center justify-between font-mono text-[11px]"
                                >
                                    <span className="text-dark-500">{scan.trigger}</span>
                                    <span className="text-dark-400">
                                        {scan.scanned} scanned ·{' '}
                                        <span className={scan.published ? 'text-primary-300' : ''}>
                                            {scan.published} found
                                        </span>
                                    </span>
                                </div>
                            ))}
                            {!status?.recent_scans?.length && (
                                <p className="text-center text-xs text-dark-500">No sweeps yet.</p>
                            )}
                        </div>
                    </HudPanel>
                </div>
            </div>
        </div>
    )
}

function Opportunity({ opportunity: o }) {
    const [expanded, setExpanded] = useState(false)
    const verdict = VERDICT[o.verdict] || VERDICT.watch
    const snapshot = o.snapshot || {}

    return (
        <div className="transition-colors hover:bg-primary-500/5">
            <button
                onClick={() => setExpanded((v) => !v)}
                className="flex w-full items-center gap-4 px-4 py-3 text-left"
            >
                <div className="w-32 shrink-0">
                    <div className="font-mono text-sm text-dark-100">{o.symbol}</div>
                    <div className="truncate text-[10px] text-dark-600">{snapshot.name}</div>
                </div>

                <span className={verdict.style}>{o.verdict?.replace('_', ' ')}</span>

                <div className="w-24 shrink-0">
                    <Meter value={o.conviction || 0} tone={verdict.tone} />
                    <div className="mt-1 font-mono text-[10px] text-dark-500">{o.conviction}% conviction</div>
                </div>

                <p className="hidden min-w-0 flex-1 truncate text-xs text-dark-400 md:block">
                    {o.thesis}
                </p>

                <div className="shrink-0 text-right">
                    <div className="font-mono text-xs tabular-nums text-dark-200">
                        ₹{Number(snapshot.price || 0).toLocaleString('en-IN')}
                    </div>
                    <Delta value={snapshot.change_percent} className="text-[10px]" />
                </div>
            </button>

            {expanded && (
                <div className="animate-fade-in space-y-4 border-t border-dark-800/60 bg-dark-950/40 px-4 py-4">
                    <p className="text-sm leading-relaxed text-dark-200">{o.thesis}</p>

                    <div className="grid gap-4 sm:grid-cols-2">
                        {o.reasons?.length > 0 && (
                            <div>
                                <div className="hud-label mb-1.5">Why</div>
                                <ul className="space-y-1">
                                    {o.reasons.map((r, i) => (
                                        <li key={i} className="text-xs leading-relaxed text-dark-300">
                                            • {r}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                        {o.risks?.length > 0 && (
                            <div>
                                <div className="hud-label mb-1.5 !text-danger-400/80">Risks</div>
                                <ul className="space-y-1">
                                    {o.risks.map((r, i) => (
                                        <li key={i} className="text-xs leading-relaxed text-dark-300">
                                            • {r}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </div>

                    <div className="flex flex-wrap gap-4 border-t border-dark-800/60 pt-3">
                        {o.entry_zone && <Stat label="Entry" value={o.entry_zone} />}
                        {o.stop_loss && <Stat label="Stop" value={`₹${o.stop_loss}`} tone="down" />}
                        {o.targets?.length > 0 && (
                            <Stat label="Targets" value={o.targets.map((t) => `₹${t}`).join(' · ')} tone="up" />
                        )}
                        <Stat label="Horizon" value={o.horizon} mono={false} tone="muted" />
                    </div>

                    {o.invalidation && (
                        <p className="text-xs text-alert-300">
                            <span className="hud-label !text-alert-400/80">Wrong if </span>
                            {o.invalidation}
                        </p>
                    )}

                    {o.triggers?.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                            {o.triggers.map((t, i) => (
                                <span key={i} className="badge-muted">
                                    {t}
                                </span>
                            ))}
                        </div>
                    )}

                    <p className="font-mono text-[9px] uppercase tracking-widest text-dark-600">
                        {o.engine} · score {o.score} · {new Date(o.created_at).toLocaleString('en-IN')}
                    </p>
                </div>
            )}
        </div>
    )
}

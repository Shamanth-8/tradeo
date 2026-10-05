import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Brain, CalendarClock, Play, Power, Radar, RefreshCw, Repeat, TrendingUp, Zap } from 'lucide-react'
import { HudPanel, Loading } from './hud/HudPanel'
import { autopilotApi } from '../services/api'

/**
 * The switches for every agent that opens paper trades on its own. Every buy
 * also passes the account-level limits in RiskPanel. Results are on the Paper
 * Trading page, split by agent.
 */

const ICON = { watchtower: Radar, momentum: Repeat, intraday: Zap, swing: TrendingUp, 'daily-pick': CalendarClock, 'fly-rl': Brain }
const rupees = (v) => `${v >= 0 ? '+' : '−'}₹${Math.abs(Number(v || 0)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

export default function TradingAgents() {
    const [agents, setAgents] = useState(null)
    const [busy, setBusy] = useState(null)
    const [note, setNote] = useState({})
    const [drafts, setDrafts] = useState({})

    const load = useCallback(() => {
        autopilotApi.agents().then(({ data }) => setAgents(data)).catch(() => {})
    }, [])
    useEffect(load, [load])

    const act = async (id, fn, message) => {
        setBusy(id)
        try {
            const { data } = await fn()
            setNote((n) => ({ ...n, [id]: message ? message(data) : null }))
            load()
        } catch (err) {
            setNote((n) => ({ ...n, [id]: err?.response?.data?.detail || err.message }))
        } finally {
            setBusy(null)
        }
    }

    if (!agents) return <Loading rows={2} label="Trading agents" />

    return (
        <HudPanel
            title="Automation"
            subtitle="Everything starts OFF and trades on paper only. Watchtower scans the market; the others open trades. Each card shows how it tested. Results are on the Paper Trading page."
        >
            <div className="stagger grid gap-4 lg:grid-cols-3">
                {agents.map((a) => {
                    const Icon = ICON[a.id] || Brain
                    const draft = drafts[a.id] || {}
                    const s = { ...a.settings, ...draft }
                    const dirty = Object.keys(draft).length > 0
                    const r = a.record || null
                    return (
                        <div key={a.id} className={`rounded-lg border p-4 space-y-3 transition-colors duration-300 ${a.enabled ? 'live-pulse border-success-400/30 bg-success-500/5' : 'border-dark-700/70 bg-dark-900/40 hover:border-primary-400/30'}`}>
                            <div className="flex items-center justify-between gap-3">
                                <div className="flex items-center gap-2">
                                    <Icon className="h-5 w-5 text-primary-400" />
                                    <span className="font-semibold text-white">{a.name}</span>
                                </div>
                                <button
                                    disabled={busy === a.id || a.ready === false}
                                    onClick={() => act(a.id, () => autopilotApi.updateAgent(a.id, { enabled: !a.enabled }))}
                                    className={`${a.enabled ? 'btn-success' : 'btn-secondary'} flex items-center gap-1.5 text-xs`}
                                >
                                    <Power className="h-3.5 w-3.5" /> {a.enabled ? 'ON' : 'OFF'}
                                </button>
                            </div>

                            <p className="text-xs text-dark-300">{a.how}</p>
                            {a.error && <p className="text-xs text-alert-300">{a.error}</p>}
                            {a.enabled && a.needs && <p className="text-xs text-alert-300">{a.needs}</p>}
                            {a.kind === 'scanner' && a.status && (
                                <p className="text-xs text-dark-400">
                                    {a.status.sweeping ? 'Scanning now…' : `${a.status.runs || 0} scans since start`}
                                    {a.status.last_run_at ? ` · last ${a.status.last_run_at.slice(11, 16)}` : ''}
                                </p>
                            )}

                            {a.id === 'daily-pick' && (
                                <div className="grid grid-cols-2 gap-3 text-xs">
                                    <Rule label={`Minimum score: ${s.min_score}`} hint="lower = more picks">
                                        <input type="range" min="50" max="90" step="1" value={s.min_score}
                                            onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: { ...draft, min_score: Number(e.target.value) } }))} className="w-full" />
                                    </Rule>
                                    <Rule label={`Picks per day: ${s.picks_per_day}`}>
                                        <input type="range" min="1" max="5" step="1" value={s.picks_per_day}
                                            onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: { ...draft, picks_per_day: Number(e.target.value) } }))} className="w-full" />
                                    </Rule>
                                    <Rule label={`Max size: ${s.position_pct}% each`} hint="risk sets the actual size">
                                        <input type="range" min="0.5" max="10" step="0.5" value={s.position_pct}
                                            onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: { ...draft, position_pct: Number(e.target.value) } }))} className="w-full" />
                                    </Rule>
                                    <Rule label="Run at (IST)">
                                        <input type="time" min="09:15" max="15:15" value={s.run_at}
                                            onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: { ...draft, run_at: e.target.value } }))}
                                            className="input-field !py-1 !text-xs" />
                                    </Rule>
                                    {dirty && (
                                        <button disabled={busy === a.id} className="btn-primary col-span-2 text-xs"
                                            onClick={() => act(a.id, () => autopilotApi.updateAgent(a.id, draft), () => {
                                                setDrafts((d) => ({ ...d, [a.id]: undefined }))
                                                return 'Rules saved.'
                                            })}>
                                            Save rules
                                        </button>
                                    )}
                                </div>
                            )}

                            {(a.id === 'intraday' || a.id === 'swing') && (
                                <div className="grid grid-cols-2 gap-3 text-xs">
                                    <Rule label={`Max open: ${s.max_open}`}>
                                        <input type="range" min="1" max="10" step="1" value={s.max_open}
                                            onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: { ...draft, max_open: Number(e.target.value) } }))} className="w-full" />
                                    </Rule>
                                    {a.id === 'intraday' && (
                                        <Rule label={`Max trades a day: ${s.max_trades_per_day}`}>
                                            <input type="range" min="1" max="30" step="1" value={s.max_trades_per_day}
                                                onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: { ...draft, max_trades_per_day: Number(e.target.value) } }))} className="w-full" />
                                        </Rule>
                                    )}
                                    <Rule label={`Max size: ${s.position_pct}% each`} hint="risk sets the actual size">
                                        <input type="range" min="0.5" max="10" step="0.5" value={s.position_pct}
                                            onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: { ...draft, position_pct: Number(e.target.value) } }))} className="w-full" />
                                    </Rule>
                                    {dirty && (
                                        <button disabled={busy === a.id} className="btn-primary col-span-2 text-xs"
                                            onClick={() => act(a.id, () => autopilotApi.updateAgent(a.id, draft), () => {
                                                setDrafts((d) => ({ ...d, [a.id]: undefined }))
                                                return 'Saved.'
                                            })}>
                                            Save
                                        </button>
                                    )}
                                </div>
                            )}

                            {a.id === 'momentum' && (
                                <div className="grid grid-cols-2 gap-3 text-xs">
                                    <Rule label={`Holdings: ${s.top}`}>
                                        <input type="range" min="5" max="15" step="1" value={s.top}
                                            onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: { ...draft, top: Number(e.target.value) } }))} className="w-full" />
                                    </Rule>
                                    <Rule label={`Capital: ${s.capital_pct}% of account`}>
                                        <input type="range" min="10" max="100" step="5" value={s.capital_pct}
                                            onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: { ...draft, capital_pct: Number(e.target.value) } }))} className="w-full" />
                                    </Rule>
                                    {a.last_rebalance && <p className="col-span-2 text-dark-500">Last rebalance: {a.last_rebalance}</p>}
                                    {dirty && (
                                        <button disabled={busy === a.id} className="btn-primary col-span-2 text-xs"
                                            onClick={() => act(a.id, () => autopilotApi.updateAgent(a.id, draft), () => {
                                                setDrafts((d) => ({ ...d, [a.id]: undefined }))
                                                return 'Saved.'
                                            })}>
                                            Save
                                        </button>
                                    )}
                                </div>
                            )}

                            {a.id === 'fly-rl' && a.settings && (
                                <p className="text-xs text-dark-400">
                                    Up to {a.settings.slots} open · {a.settings.position_pct}% each · approves the top {100 - a.settings.approve_percentile}% ·
                                    holds up to {a.settings.hold_days} days
                                </p>
                            )}

                            {r && <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-dark-800/60 pt-2 text-xs text-dark-300">
                                <span>{r.trades} trades</span>
                                <span>{r.open} open</span>
                                <span className="text-success-400">{r.wins} won</span>
                                <span className="text-danger-400">{r.losses} lost</span>
                                <span className={r.realised_pnl > 0 ? 'text-success-400' : r.realised_pnl < 0 ? 'text-danger-400' : ''}>
                                    realised {rupees(r.realised_pnl)}
                                </span>
                            </div>}
                            {a.backtest && <p className="text-[11px] text-dark-500">Tested: {a.backtest}</p>}

                            <div className="flex flex-wrap items-center gap-2">
                                <button disabled={!a.enabled || busy === a.id} className="btn-secondary flex items-center gap-1.5 text-xs"
                                    onClick={() => act(a.id, () => autopilotApi.runAgent(a.id), (d) =>
                                        d.ok || d.started ? (d.symbol ? `Bought ${d.symbol}.` : d.market || 'Started — decisions appear on Paper Trading.') : d.error)}>
                                    {busy === a.id ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} {a.kind === 'scanner' ? 'Scan now' : 'Run now'}
                                </button>
                                <Link to="/paper-trading" className="text-xs text-primary-300 hover:underline">Results on Paper Trading →</Link>
                            </div>
                            {note[a.id] && <p className="text-xs text-dark-300">{note[a.id]}</p>}
                        </div>
                    )
                })}
            </div>
        </HudPanel>
    )
}

function Rule({ label, hint, children }) {
    return (
        <label className="space-y-1">
            <span className="block text-dark-300">{label}{hint && <span className="text-dark-500"> · {hint}</span>}</span>
            {children}
        </label>
    )
}

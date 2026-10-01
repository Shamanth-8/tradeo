import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
    ArrowLeft, Ban, Check, Loader2, Network, Play, RotateCcw, Search, Users, X,
} from 'lucide-react'
import ResearchNav, { LocalModelNote, useEngine } from '../components/research/ResearchNav'
import Markdown from '../components/research/Markdown'
import { HudPanel, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import { researchApi } from '../services/api'

/**
 * Swarm: a team of specialist agents working as a dependency graph. Analysts
 * run in parallel, then a lead writes the final report from their summaries.
 * Every agent has Tradeo's tools for Indian data.
 */

const STATUS = {
    pending: { cls: 'border-dark-700 text-dark-400', label: 'waiting' },
    blocked: { cls: 'border-dark-700 text-dark-500', label: 'blocked' },
    in_progress: { cls: 'border-primary-400/60 text-primary-200 shadow-glow', label: 'running' },
    completed: { cls: 'border-success-400/40 text-success-300', label: 'done' },
    failed: { cls: 'border-danger-400/50 text-danger-300', label: 'failed' },
    cancelled: { cls: 'border-dark-600 text-dark-500', label: 'cancelled' },
}

const INDIA_VARS = {
    market: 'Indian equities (NSE)',
    target: 'NIFTY 50',
    ticker: 'RELIANCE.NS',
    symbol: 'RELIANCE.NS',
    commodity: 'gold',
    horizon: '3 months',
}

export default function Swarm() {
    const [engine, reloadEngine] = useEngine()
    const [presets, setPresets] = useState(null)
    const [runs, setRuns] = useState([])
    const [query, setQuery] = useState('')
    const [launching, setLaunching] = useState(null)
    const [openRun, setOpenRun] = useState(null)
    const [error, setError] = useState(null)

    const loadRuns = useCallback(() => researchApi.swarmRuns().then((r) => setRuns(r.data || [])).catch(() => {}), [])

    useEffect(() => {
        if (!engine?.online) return
        researchApi.swarmPresets().then((r) => setPresets(r.data || [])).catch((e) => setError(e.message))
        loadRuns()
    }, [engine?.online, loadRuns])

    const shown = useMemo(() => {
        const q = query.toLowerCase()
        return (presets || []).filter((p) => `${p.title} ${p.description}`.toLowerCase().includes(q))
    }, [presets, query])

    const start = async (preset, vars) => {
        try {
            const res = await researchApi.startSwarm(preset.name, vars)
            setLaunching(null)
            setOpenRun(res.data.id)
            loadRuns()
        } catch (e) {
            setError(e?.response?.data?.detail || e.message)
        }
    }

    if (openRun) {
        return (
            <div className="space-y-4 animate-fade-in">
                <ResearchNav title="Swarm" engine={engine} onEngineChange={reloadEngine} />
                <RunView id={openRun} onBack={() => { setOpenRun(null); loadRuns() }} />
            </div>
        )
    }

    return (
        <div className="space-y-4 animate-fade-in">
            <ResearchNav
                title="Swarm"
                subtitle="Launch a team of specialist agents. Analysts work in parallel; a lead combines their findings into one report."
                engine={engine}
                onEngineChange={reloadEngine}
            />
            <LocalModelNote engine={engine} />
            <ErrorNote error={error} onRetry={() => setError(null)} />

            {runs.length > 0 && (
                <HudPanel title="Recent teams" padded={false}>
                    <div className="divide-y divide-dark-800/70">
                        {runs.slice(0, 6).map((r) => (
                            <button key={r.id} onClick={() => setOpenRun(r.id)} className="flex w-full items-center gap-4 px-4 py-2 text-left text-sm hover:bg-primary-500/5">
                                <Network className="h-4 w-4 text-primary-400" />
                                <span className="flex-1 truncate text-dark-100">{r.preset_name.replace(/_/g, ' ')}</span>
                                <div className="hidden w-32 md:block">
                                    <div className="meter"><div className="meter-fill bg-primary-400" style={{ width: `${r.task_count ? (r.completed_count / r.task_count) * 100 : 0}%` }} /></div>
                                </div>
                                <span className="font-mono text-[11px] text-dark-500">{r.completed_count}/{r.task_count}</span>
                                <span className={`badge ${r.status === 'completed' ? 'badge-success' : r.status === 'failed' ? 'badge-danger' : r.status === 'running' ? 'badge-primary' : 'badge-muted'}`}>{r.status}</span>
                            </button>
                        ))}
                    </div>
                </HudPanel>
            )}

            <div className="relative max-w-md">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-dark-500" />
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a team" className="input-field w-full !pl-9" />
            </div>

            {!presets ? (
                <Loading rows={4} label="Loading teams" />
            ) : (
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {shown.map((p, i) => (
                        <button
                            key={p.name}
                            onClick={() => setLaunching(p)}
                            disabled={!engine?.online}
                            style={{ animationDelay: `${Math.min(i, 12) * 40}ms` }}
                            className="hud-panel group animate-slide-up p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary-400/40 hover:shadow-glow disabled:opacity-50"
                        >
                            <div className="flex items-start justify-between gap-2">
                                <h3 className="font-semibold text-dark-50 group-hover:text-primary-200">{p.title}</h3>
                                <span className="flex shrink-0 items-center gap-1 font-mono text-[10px] text-dark-500">
                                    <Users className="h-3 w-3" /> {p.agent_count}
                                </span>
                            </div>
                            <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-dark-400">{p.description}</p>
                            <div className="mt-3 flex flex-wrap gap-1">
                                {(p.variables || []).map((v) => <span key={v.name} className="badge-muted">{v.name}</span>)}
                            </div>
                        </button>
                    ))}
                </div>
            )}

            {launching && <LaunchDialog preset={launching} onClose={() => setLaunching(null)} onStart={start} />}
        </div>
    )
}

function LaunchDialog({ preset, onClose, onStart }) {
    const [vars, setVars] = useState(() =>
        Object.fromEntries((preset.variables || []).map((v) => [v.name, INDIA_VARS[v.name] || ''])),
    )
    const [busy, setBusy] = useState(false)
    const missing = (preset.variables || []).some((v) => v.required && !vars[v.name]?.trim())
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-dark-950/70 p-4 backdrop-blur-sm animate-fade-in" onClick={onClose}>
            <div className="hud-panel-glow w-full max-w-lg p-5 animate-slide-up" onClick={(e) => e.stopPropagation()}>
                <div className="flex items-start justify-between">
                    <div>
                        <div className="hud-label">Launch team</div>
                        <h2 className="mt-1 text-lg font-semibold text-dark-50">{preset.title}</h2>
                    </div>
                    <button onClick={onClose} className="text-dark-500 hover:text-dark-100"><X className="h-5 w-5" /></button>
                </div>
                <p className="mt-2 text-xs text-dark-400">{preset.description}</p>
                <form
                    className="mt-4 space-y-3"
                    onSubmit={async (e) => {
                        e.preventDefault()
                        setBusy(true)
                        await onStart(preset, vars)
                        setBusy(false)
                    }}
                >
                    {(preset.variables || []).map((v) => (
                        <label key={v.name} className="block">
                            <span className="hud-label mb-1 block">{v.name}{v.required && <span className="text-danger-400"> *</span>}</span>
                            <input
                                value={vars[v.name]}
                                onChange={(e) => setVars({ ...vars, [v.name]: e.target.value })}
                                placeholder={v.description}
                                className="input-field w-full"
                            />
                            <span className="mt-0.5 block text-[11px] text-dark-500">{v.description}</span>
                        </label>
                    ))}
                    <button disabled={missing || busy} className="btn-primary flex w-full items-center justify-center gap-2 disabled:opacity-50">
                        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Launch {preset.agent_count} agents
                    </button>
                </form>
            </div>
        </div>
    )
}

function RunView({ id, onBack }) {
    const [run, setRun] = useState(null)
    const [live, setLive] = useState({}) // task/agent id → { tool, text }
    const [error, setError] = useState(null)
    const sourceRef = useRef(null)

    const load = useCallback(() => researchApi.swarmRun(id).then((r) => setRun(r.data)).catch((e) => setError(e.message)), [id])

    useEffect(() => {
        load()
        const source = researchApi.swarmEvents(id)
        sourceRef.current = source
        const handle = (e) => {
            let evt
            try {
                evt = JSON.parse(e.data)
            } catch {
                return
            }
            const key = evt.task_id || evt.agent_id
            const d = evt.data || {}
            if (key) {
                setLive((l) => {
                    const cur = l[key] || {}
                    if (evt.type === 'tool_call') return { ...l, [key]: { ...cur, tool: d.tool } }
                    if (evt.type === 'worker_text') {
                        const last = String(d.content || '').trim().split('\n').filter(Boolean).pop()
                        return last ? { ...l, [key]: { ...cur, text: last.slice(0, 200) } } : l
                    }
                    return l
                })
            }
            if (/^(task_|worker_|run_|layer_)/.test(evt.type) && !['task_heartbeat', 'worker_text'].includes(evt.type)) load()
        }
        const types = ['run_started', 'run_completed', 'run_error', 'layer_started', 'task_started', 'task_completed', 'task_failed',
            'task_blocked', 'task_cancelled', 'task_retry', 'worker_started', 'worker_completed', 'worker_failed', 'worker_text', 'tool_call', 'tool_result']
        types.forEach((t) => source.addEventListener(t, handle))
        source.addEventListener('done', () => {
            load()
            source.close()
        })
        return () => source.close()
    }, [id, load])

    // DAG layers from depends_on, so the graph reads left to right.
    const layers = useMemo(() => {
        const tasks = run?.tasks || []
        const depth = {}
        const byId = Object.fromEntries(tasks.map((t) => [t.id, t]))
        const d = (t, seen = new Set()) => {
            if (depth[t.id] !== undefined) return depth[t.id]
            if (seen.has(t.id)) return 0
            seen.add(t.id)
            const parents = (t.depends_on || []).map((p) => byId[p]).filter(Boolean)
            depth[t.id] = parents.length ? 1 + Math.max(...parents.map((p) => d(p, seen))) : 0
            return depth[t.id]
        }
        tasks.forEach((t) => d(t))
        const out = []
        tasks.forEach((t) => (out[depth[t.id]] ||= []).push(t))
        return out
    }, [run])

    if (!run) return error ? <ErrorNote error={error} /> : <Loading rows={4} label="Loading team" />
    const agents = Object.fromEntries((run.agents || []).map((a) => [a.id, a]))
    const running = ['running', 'pending'].includes(run.status)

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <button onClick={onBack} className="flex items-center gap-1 text-xs text-dark-400 hover:text-primary-300">
                    <ArrowLeft className="h-3 w-3" /> All teams
                </button>
                <div className="flex gap-2">
                    {running ? (
                        <button onClick={() => researchApi.cancelSwarm(id).then(load)} className="btn-danger flex items-center gap-1 !py-1 text-xs"><Ban className="h-3 w-3" /> Stop</button>
                    ) : (
                        run.status !== 'completed' && <button onClick={() => researchApi.retrySwarm(id).then(load)} className="btn-ghost flex items-center gap-1 !py-1 text-xs"><RotateCcw className="h-3 w-3" /> Retry</button>
                    )}
                </div>
            </div>

            <HudPanel
                title={run.preset_name.replace(/_/g, ' ')}
                subtitle={Object.entries(run.user_vars || {}).map(([k, v]) => `${k}: ${v}`).join(' · ')}
                right={<span className={`badge ${run.status === 'completed' ? 'badge-success' : run.status === 'failed' ? 'badge-danger' : 'badge-primary'}`}>{run.status}</span>}
                glow={running}
            >
                <div className="flex gap-4 overflow-x-auto pb-2">
                    {layers.map((layer, i) => (
                        <div key={i} className="flex min-w-[16rem] flex-1 flex-col gap-3">
                            <div className="hud-label">{i === layers.length - 1 && i > 0 ? 'Synthesis' : `Stage ${i + 1}`}</div>
                            {layer.map((t) => {
                                const st = STATUS[t.status] || STATUS.pending
                                const info = live[t.id] || live[t.agent_id] || {}
                                return (
                                    <div key={t.id} className={`rounded-lg border bg-dark-950/60 p-3 transition-all duration-300 ${st.cls}`}>
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="truncate text-sm font-medium text-dark-100">{agents[t.agent_id]?.role || t.agent_id}</span>
                                            <span className="flex items-center gap-1 font-mono text-[10px] uppercase">
                                                {t.status === 'in_progress' ? <Loader2 className="h-3 w-3 animate-spin" /> : t.status === 'completed' ? <Check className="h-3 w-3" /> : null}
                                                {st.label}
                                            </span>
                                        </div>
                                        <div className="mt-0.5 font-mono text-[10px] text-dark-500">{t.id}</div>
                                        {t.status === 'in_progress' && (info.tool || info.text) && (
                                            <div className="mt-2 space-y-1 text-[11px]">
                                                {info.tool && <div className="font-mono text-primary-300">⚙ {String(info.tool).replace(/^mcp_tradeo_/, 'tradeo · ')}</div>}
                                                {info.text && <div className="line-clamp-2 text-dark-400">{info.text}</div>}
                                            </div>
                                        )}
                                        {t.summary && t.status === 'completed' && (
                                            <details className="mt-2 text-xs text-dark-300">
                                                <summary className="cursor-pointer text-dark-500 hover:text-primary-300">Summary</summary>
                                                <div className="mt-1 max-h-48 overflow-y-auto"><Markdown className="!text-xs">{t.summary}</Markdown></div>
                                            </details>
                                        )}
                                        {t.error && <div className="mt-2 text-[11px] text-danger-300">{t.error}</div>}
                                    </div>
                                )
                            })}
                        </div>
                    ))}
                </div>
            </HudPanel>

            {run.final_report ? (
                <HudPanel title="Final report" glow>
                    <Markdown>{run.final_report}</Markdown>
                </HudPanel>
            ) : (
                !running && <Empty icon={Network} title="No report" hint="The team stopped before the synthesis step." />
            )}
        </div>
    )
}

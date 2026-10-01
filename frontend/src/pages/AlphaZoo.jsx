import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Check, Code2, Loader2, Play, Scale, Search, Sigma, X } from 'lucide-react'
import ResearchNav, { Banner, useEngine } from '../components/research/ResearchNav'
import { HudPanel, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import { researchApi } from '../services/api'
import { Chip, Field, Segmented } from '../components/research/Controls'

/**
 * Alpha Zoo: 460+ published cross-sectional factors (Alpha101, GTJA 191,
 * Qlib 158, Fama-French/Carhart), benchmarked by information coefficient.
 *
 * "nifty" is Tradeo's own NSE equity universe, so a factor can be judged on
 * the market Tradeo trades rather than on US or Chinese stocks.
 */

const UNIVERSES = [
    { id: 'nifty', label: 'Nifty (Tradeo NSE)', tag: 'equity_in' },
    { id: 'sp500', label: 'S&P 500', tag: 'equity_us' },
    { id: 'csi300', label: 'CSI 300 (needs Tushare)', tag: 'equity_cn' },
]
const ZOOS = ['academic', 'alpha101', 'gtja191', 'qlib158', 'fundamental']
const PERIODS = ['2023-2025', '2022-2024', '2020-2024', '2018-2024']
const CATEGORY = {
    alive: 'text-success-300 border-success-400/30 bg-success-500/10',
    reversed: 'text-alert-300 border-alert-400/30 bg-alert-500/10',
    dead: 'text-dark-400 border-dark-700 bg-dark-800/40',
}

export default function AlphaZoo() {
    const [engine, reloadEngine] = useEngine()
    const navigate = useNavigate()
    const [alphas, setAlphas] = useState(null)
    const [query, setQuery] = useState('')
    const [zoo, setZoo] = useState('')
    const [theme, setTheme] = useState('')
    const [indiaOnly, setIndiaOnly] = useState(true)
    const [selected, setSelected] = useState([])
    const [detail, setDetail] = useState(null)
    const [error, setError] = useState(null)

    const [universe, setUniverse] = useState('nifty')
    const [period, setPeriod] = useState('2022-2024')
    const [job, setJob] = useState(null) // { kind, progress, result, status }
    const sourceRef = useRef(null)

    useEffect(() => {
        if (!engine?.online || alphas) return
        researchApi
            .alphas()
            .then((r) => setAlphas(r.data.alphas || []))
            .catch((e) => setError(e?.response?.data?.detail || e.message))
    }, [engine?.online, alphas])

    useEffect(() => () => sourceRef.current?.close(), [])

    const themes = useMemo(() => {
        const counts = {}
        for (const a of alphas || []) for (const t of a.theme || []) counts[t] = (counts[t] || 0) + 1
        return Object.entries(counts).sort((a, b) => b[1] - a[1])
    }, [alphas])

    const shown = useMemo(() => {
        const q = query.trim().toLowerCase()
        return (alphas || []).filter(
            (a) =>
                (!zoo || a.zoo === zoo) &&
                (!theme || (a.theme || []).includes(theme)) &&
                (!indiaOnly || (a.universe || []).includes('equity_in')) &&
                (!q || `${a.id} ${a.nickname || ''}`.toLowerCase().includes(q)),
        )
    }, [alphas, query, zoo, theme, indiaOnly])

    const toggle = (id) =>
        setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= 12 ? s : [...s, id]))

    const openDetail = async (id) => {
        setDetail({ id, loading: true })
        try {
            setDetail((await researchApi.alpha(id)).data)
        } catch (e) {
            setDetail(null)
            setError(e?.response?.data?.detail || e.message)
        }
    }

    const stream = (kind, jobId, open) => {
        sourceRef.current?.close()
        let finished = false
        const source = open(jobId)
        sourceRef.current = source
        const parse = (e) => {
            try {
                return JSON.parse(e.data)
            } catch {
                return {}
            }
        }
        source.addEventListener('progress', (e) => setJob((j) => ({ ...j, progress: parse(e) })))
        source.addEventListener('result', (e) => setJob((j) => ({ ...j, result: parse(e) })))
        source.addEventListener('done', () => {
            finished = true
            setJob((j) => ({ ...j, status: j.status === 'error' ? 'error' : 'done' }))
            source.close()
        })
        source.addEventListener('error', (e) => {
            if (finished) return source.close()
            const data = e.data ? parse(e) : {}
            setJob((j) => ({ ...j, status: 'error', error: data.message || 'The job stream closed early.' }))
            source.close()
        })
        setJob({ kind, status: 'running', progress: null, result: null })
    }

    const runBench = async () => {
        if (!zoo) return setError('Pick a zoo to bench (the whole zoo is scored).')
        setError(null)
        try {
            const res = await researchApi.bench({ zoo, universe, period, top: 10 })
            stream('bench', res.data.job_id, researchApi.benchStream)
        } catch (e) {
            setError(e?.response?.data?.detail?.[0]?.msg || e?.response?.data?.detail || e.message)
        }
    }

    const runCompare = async () => {
        if (selected.length < 2) return setError('Select at least two factors to compare.')
        setError(null)
        try {
            const res = await researchApi.compareAlphas({ alpha_ids: selected, universe, period, sort: 'ir' })
            stream('compare', res.data.job_id, researchApi.compareStream)
        } catch (e) {
            setError(e?.response?.data?.detail?.[0]?.msg || e?.response?.data?.detail || e.message)
        }
    }

    const askAgent = (ids) =>
        navigate(`/research?q=${encodeURIComponent(`Explain the factors ${ids.join(', ')} and backtest the strongest one on Tradeo's NSE universe for ${period}.`)}`)

    return (
        <div className="space-y-4 animate-fade-in">
            <ResearchNav
                title="Alpha Zoo"
                subtitle="460+ published factors, scored by information coefficient on Indian, US or Chinese stocks. Select factors to compare them head to head."
                engine={engine}
                onEngineChange={reloadEngine}
            />
            <ErrorNote error={error} onRetry={() => setError(null)} />

            {/* Controls */}
            <HudPanel padded>
                <div className="flex flex-wrap items-center gap-3">
                    <div className="relative min-w-[14rem] flex-1">
                        <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-dark-500" />
                        <input
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Search factors (momentum, carhart, alpha101_012…)"
                            className="input-field w-full !pl-9"
                        />
                    </div>
                    <Segmented
                        value={zoo}
                        onChange={setZoo}
                        options={[{ id: '', label: 'All' }, ...ZOOS.map((z) => ({ id: z, label: z }))]}
                    />
                    <label className="flex cursor-pointer items-center gap-2 text-xs text-dark-300">
                        <input type="checkbox" checked={indiaOnly} onChange={(e) => setIndiaOnly(e.target.checked)} className="accent-cyan-400" />
                        Works on Indian equities
                    </label>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                    <Chip active={!theme} onClick={() => setTheme('')}>all themes</Chip>
                    {themes.map(([t, n]) => (
                        <Chip key={t} active={theme === t} onClick={() => setTheme(theme === t ? '' : t)}>
                            {t} <span className="text-dark-500">{n}</span>
                        </Chip>
                    ))}
                </div>
            </HudPanel>

            <div className="grid gap-4 xl:grid-cols-[1fr_24rem]">
                {/* Library */}
                <HudPanel
                    title={`${shown.length} factors`}
                    subtitle="Click a row for its formula and code; tick to compare"
                    padded={false}
                    right={
                        selected.length > 0 && (
                            <button onClick={() => setSelected([])} className="btn-ghost !px-2 !py-1 !text-[10px]">
                                Clear {selected.length}
                            </button>
                        )
                    }
                >
                    {!alphas ? (
                        <div className="p-4"><Loading rows={6} label="Loading the zoo" /></div>
                    ) : shown.length === 0 ? (
                        <Empty icon={Sigma} title="No factors match" hint="Loosen the filters." />
                    ) : (
                        <div className="max-h-[36rem] overflow-y-auto">
                            {shown.slice(0, 300).map((a) => {
                                const on = selected.includes(a.id)
                                return (
                                    <div
                                        key={a.id}
                                        className={`flex items-center gap-3 border-b border-dark-800/60 px-4 py-2 transition-colors hover:bg-primary-500/5 ${on ? 'bg-primary-500/10' : ''}`}
                                    >
                                        <button
                                            onClick={() => toggle(a.id)}
                                            className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${on ? 'border-primary-400 bg-primary-500 text-dark-950' : 'border-dark-600'}`}
                                        >
                                            {on && <Check className="h-3 w-3" />}
                                        </button>
                                        <button onClick={() => openDetail(a.id)} className="min-w-0 flex-1 text-left">
                                            <div className="truncate font-mono text-xs text-primary-200">{a.id}</div>
                                            <div className="truncate text-xs text-dark-400">{a.nickname || '—'}</div>
                                        </button>
                                        <div className="hidden shrink-0 gap-1 md:flex">
                                            {(a.theme || []).slice(0, 2).map((t) => (
                                                <span key={t} className="badge-muted">{t}</span>
                                            ))}
                                        </div>
                                        <span className="w-14 shrink-0 text-right font-mono text-[10px] text-dark-500">
                                            {a.decay_horizon ? `${a.decay_horizon}d` : ''}
                                        </span>
                                    </div>
                                )
                            })}
                        </div>
                    )}
                </HudPanel>

                {/* Bench / compare */}
                <div className="space-y-4">
                    <HudPanel title="Benchmark" subtitle="Score a whole zoo, or compare your selection">
                        <div className="space-y-3">
                            <Field label="Universe">
                                <select value={universe} onChange={(e) => setUniverse(e.target.value)} className="input-field w-full">
                                    {UNIVERSES.map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}
                                </select>
                            </Field>
                            <Field label="Period">
                                <Segmented value={period} onChange={setPeriod} options={PERIODS.map((p) => ({ id: p, label: p }))} />
                            </Field>
                            <div className="grid grid-cols-2 gap-2">
                                <button onClick={runBench} disabled={!engine?.online || job?.status === 'running'} className="btn-primary flex items-center justify-center gap-2 disabled:opacity-50">
                                    <Play className="h-3.5 w-3.5" /> Bench {zoo || 'zoo'}
                                </button>
                                <button onClick={runCompare} disabled={!engine?.online || job?.status === 'running'} className="btn-ghost flex items-center justify-center gap-2 disabled:opacity-50">
                                    <Scale className="h-3.5 w-3.5" /> Compare {selected.length || ''}
                                </button>
                            </div>
                            {universe === 'nifty' && (
                                <p className="text-[11px] text-dark-500">
                                    Nifty = Tradeo's 67 NSE equities with their sectors. Current members only, so results carry survivorship bias.
                                </p>
                            )}
                        </div>
                    </HudPanel>

                    {job && <JobResult job={job} onAsk={askAgent} onOpen={openDetail} />}
                </div>
            </div>

            {detail && <AlphaDrawer detail={detail} onClose={() => setDetail(null)} onAsk={askAgent} />}
        </div>
    )
}

function JobResult({ job, onAsk, onOpen }) {
    const p = job.progress
    const pct = p?.n_total ? Math.round((p.n_done / p.n_total) * 100) : null
    const r = job.result
    return (
        <HudPanel title={job.kind === 'bench' ? 'Bench result' : 'Comparison'} glow={job.status === 'running'}>
            {job.status === 'running' && (
                <div className="space-y-2">
                    <div className="flex items-center justify-between text-xs text-dark-400">
                        <span className="flex items-center gap-2"><Loader2 className="h-3 w-3 animate-spin" /> {p?.current_alpha_id || 'Loading prices'}</span>
                        <span className="font-mono">{p?.n_total ? `${p.n_done}/${p.n_total}` : ''}</span>
                    </div>
                    <div className="meter"><div className="meter-fill bg-primary-400 transition-all" style={{ width: `${pct ?? 5}%` }} /></div>
                </div>
            )}
            {job.status === 'error' && <Banner tone="danger">{job.error}</Banner>}
            {r && job.kind === 'bench' && (
                <div className="space-y-4">
                    <div className="flex h-3 overflow-hidden rounded-full bg-dark-800">
                        {['alive', 'reversed', 'dead'].map((k) => {
                            const total = (r.alive || 0) + (r.reversed || 0) + (r.dead || 0) || 1
                            const color = { alive: 'bg-success-400', reversed: 'bg-alert-400', dead: 'bg-dark-600' }[k]
                            return <div key={k} className={`${color} transition-all duration-700`} style={{ width: `${(r[k] / total) * 100}%` }} title={`${k}: ${r[k]}`} />
                        })}
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-center">
                        {['alive', 'reversed', 'dead'].map((k) => (
                            <div key={k} className={`rounded border px-2 py-1.5 ${CATEGORY[k]}`}>
                                <div className="font-mono text-lg">{r[k] ?? 0}</div>
                                <div className="text-[10px] uppercase tracking-wider">{k}</div>
                            </div>
                        ))}
                    </div>
                    <div>
                        <div className="hud-label mb-1">Top by IR</div>
                        {(r.top5_by_ir || []).map((row) => (
                            <button key={row.id} onClick={() => onOpen(row.id)} className="flex w-full items-center justify-between rounded px-1 py-1 text-xs hover:bg-dark-800/60">
                                <span className="truncate font-mono text-primary-200">{row.id}</span>
                                <span className="font-mono text-dark-300">IR {Number(row.ir).toFixed(3)} · IC {Number(row.ic_mean).toFixed(4)}</span>
                            </button>
                        ))}
                    </div>
                    {r.alive === 0 && (
                        <p className="text-[11px] text-dark-500">
                            Nothing cleared the bar. On Indian large caps that matches Tradeo's own finding: no simple factor predicts short-horizon ranks.
                        </p>
                    )}
                    <button onClick={() => onAsk((r.top5_by_ir || []).slice(0, 3).map((x) => x.id))} className="btn-ghost flex w-full items-center justify-center gap-2 text-xs">
                        <Bot className="h-3.5 w-3.5" /> Ask the agent about the top factors
                    </button>
                </div>
            )}
            {r && job.kind === 'compare' && (
                <div className="space-y-2">
                    <div className="text-xs text-dark-400">Winner: <span className="font-mono text-success-300">{r.winner}</span></div>
                    {(r.ranking || []).map((row) => {
                        const max = Math.max(...r.ranking.map((x) => Math.abs(x.ir) || 0.0001))
                        return (
                            <div key={row.id} className="space-y-1">
                                <div className="flex justify-between text-xs">
                                    <span className="font-mono text-dark-200">#{row.rank} {row.id}</span>
                                    <span className="font-mono text-dark-400">IR {Number(row.ir).toFixed(3)}</span>
                                </div>
                                <div className="meter"><div className={`meter-fill ${row.ir >= 0 ? 'bg-success-400' : 'bg-danger-400'} transition-all duration-700`} style={{ width: `${(Math.abs(row.ir) / max) * 100}%` }} /></div>
                            </div>
                        )
                    })}
                    {(r.skipped || []).length > 0 && (
                        <p className="text-[11px] text-dark-500">Skipped: {r.skipped.map((s) => `${s.id} (${s.reason})`).join(', ')}</p>
                    )}
                </div>
            )}
        </HudPanel>
    )
}

function AlphaDrawer({ detail, onClose, onAsk }) {
    const meta = detail.alpha?.meta || {}
    return (
        <div className="fixed inset-0 z-50 flex justify-end bg-dark-950/60 backdrop-blur-sm animate-fade-in" onClick={onClose}>
            <aside className="h-full w-full max-w-2xl overflow-y-auto border-l border-primary-400/20 bg-dark-950 p-6 animate-slide-up" onClick={(e) => e.stopPropagation()}>
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <div className="font-mono text-sm text-primary-300">{detail.alpha?.id || detail.id}</div>
                        <h2 className="mt-1 text-lg font-semibold text-dark-50">{meta.nickname || '…'}</h2>
                    </div>
                    <button onClick={onClose} className="text-dark-500 hover:text-dark-100"><X className="h-5 w-5" /></button>
                </div>
                {detail.loading ? (
                    <Loading rows={4} label="Loading factor" />
                ) : (
                    <div className="mt-5 space-y-5">
                        <div className="flex flex-wrap gap-1.5">
                            {(meta.theme || []).map((t) => <span key={t} className="badge-primary">{t}</span>)}
                            {(meta.universe || []).map((u) => <span key={u} className="badge-muted">{u}</span>)}
                            {meta.decay_horizon && <span className="badge-muted">decay {meta.decay_horizon}d</span>}
                        </div>
                        {meta.formula_latex && (
                            <div>
                                <div className="hud-label mb-1">Formula</div>
                                <pre className="overflow-x-auto whitespace-pre-wrap rounded border border-dark-800 bg-dark-900 p-3 font-mono text-xs text-dark-200">{meta.formula_latex}</pre>
                            </div>
                        )}
                        {meta.notes && <p className="text-sm leading-relaxed text-dark-300">{meta.notes}</p>}
                        <div>
                            <div className="hud-label mb-1 flex items-center gap-1"><Code2 className="h-3 w-3" /> Source</div>
                            <pre className="max-h-96 overflow-auto rounded border border-dark-800 bg-dark-900 p-3 font-mono text-[11px] text-dark-300">{detail.source_code}</pre>
                        </div>
                        <button onClick={() => onAsk([detail.alpha.id])} className="btn-primary flex items-center gap-2">
                            <Bot className="h-4 w-4" /> Backtest it with the agent
                        </button>
                    </div>
                )}
            </aside>
        </div>
    )
}

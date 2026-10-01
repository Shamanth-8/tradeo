import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Code2, FlaskConical, RefreshCw } from 'lucide-react'
import {
    Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { HudPanel, Stat, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import { researchApi } from '../services/api'
import ResearchNav, { useEngine } from '../components/research/ResearchNav'

/**
 * Backtest runs produced by the research agent.
 *
 * Kept separate from Strategy Studio on purpose: Studio runs Tradeo's own
 * sandboxed backtester, these come from the research engine's. Both are
 * paper; neither trades.
 */

const pct = (v) =>
    v === null || v === undefined || Number.isNaN(Number(v))
        ? '—'
        : `${Number(v) >= 0 ? '+' : ''}${(Number(v) * 100).toFixed(2)}%`

const num = (v, d = 2) =>
    v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(d)

const STATUS = { success: 'badge-success', failed: 'badge-danger', running: 'badge-primary' }

export default function ResearchRuns() {
    const { runId } = useParams()
    const [engine, reloadEngine] = useEngine()
    return (
        <div className="space-y-4 animate-fade-in">
            <ResearchNav
                title="Research Runs"
                subtitle="Every task the agents ran, with backtest metrics, equity curve, trades and the strategy code where there is one."
                engine={engine}
                onEngineChange={reloadEngine}
            />
            {runId ? <RunDetail runId={runId} /> : <RunList />}
        </div>
    )
}

function RunList() {
    const [runs, setRuns] = useState(null)
    const [error, setError] = useState(null)

    const load = useCallback(async () => {
        setError(null)
        try {
            setRuns((await researchApi.runs(100)).data || [])
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
            setRuns([])
        }
    }, [])

    useEffect(() => {
        load()
    }, [load])

    return (
        <div className="space-y-4 animate-fade-in">
            <HudPanel
                title={runs ? `${runs.length} runs` : 'Runs'}
                subtitle="Click a run for its details"
                right={
                    <div className="flex gap-2">
                        <button onClick={load} className="btn-ghost !px-2 !py-1 !text-[10px]">
                            <RefreshCw className="h-3 w-3" />
                        </button>
                    </div>
                }
                padded={false}
            >
                <ErrorNote error={error} onRetry={load} />
                {runs === null ? (
                    <div className="p-4"><Loading rows={4} label="Reading runs" /></div>
                ) : runs.length === 0 ? (
                    <Empty
                        icon={FlaskConical}
                        title="No runs yet"
                        hint="Ask the research agent to backtest something and it shows up here."
                        action={<Link to="/research" className="btn-primary">Open the agent</Link>}
                    />
                ) : (
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="hud-label text-left">
                                <th className="px-4 py-2">When</th>
                                <th className="px-4 py-2">Task</th>
                                <th className="px-4 py-2">Symbols</th>
                                <th className="px-4 py-2 text-right">Return</th>
                                <th className="px-4 py-2 text-right">Sharpe</th>
                                <th className="px-4 py-2">Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {runs.map((r) => (
                                <tr key={r.run_id} className="border-t border-dark-800/70 hover:bg-dark-800/30">
                                    <td className="whitespace-nowrap px-4 py-2 font-mono text-xs text-dark-400">{r.created_at}</td>
                                    <td className="max-w-md truncate px-4 py-2">
                                        <Link to={`/research/runs/${r.run_id}`} className="text-dark-100 hover:text-primary-300">
                                            {firstLine(r.prompt) || r.run_id}
                                        </Link>
                                    </td>
                                    <td className="px-4 py-2 font-mono text-xs text-dark-400">{(r.codes || []).join(', ') || '—'}</td>
                                    <td className={`px-4 py-2 text-right font-mono ${Number(r.total_return) >= 0 ? 'text-success-400' : 'text-danger-400'}`}>
                                        {r.total_return === null ? '—' : pct(r.total_return)}
                                    </td>
                                    <td className="px-4 py-2 text-right font-mono">{num(r.sharpe)}</td>
                                    <td className="px-4 py-2">
                                        <span className={STATUS[r.status] || 'badge-muted'}>{r.status}</span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </HudPanel>
        </div>
    )
}

function firstLine(text) {
    return (text || '').split('\n').map((l) => l.replace(/^#+\s*/, '').trim()).find(Boolean)
}

// The engine's CSV columns vary by strategy, so find the date and value
// columns by shape instead of by name.
function equitySeries(rows) {
    if (!rows?.length) return null
    const keys = Object.keys(rows[0])
    const dateKey = keys.find((k) => /date|time/i.test(k)) || keys[0]
    const valueKey =
        keys.find((k) => /equity|nav|portfolio|value/i.test(k) && k !== dateKey) ||
        keys.find((k) => k !== dateKey && !Number.isNaN(Number(rows[0][k])))
    if (!valueKey) return null
    return {
        valueKey,
        data: rows.map((r) => ({ date: String(r[dateKey]).slice(0, 10), value: Number(r[valueKey]) })),
    }
}

function RunDetail({ runId }) {
    const [run, setRun] = useState(null)
    const [error, setError] = useState(null)

    useEffect(() => {
        researchApi
            .run(runId)
            .then((res) => setRun(res.data))
            .catch((err) => setError(err?.response?.data?.detail || err.message))
    }, [runId])

    if (error) return <ErrorNote error={error} />
    if (!run) return <Loading rows={5} label="Loading run" />

    const m = run.metrics
    const equity = equitySeries(run.equity_curve || run.artifacts_equity_csv)
    const trades = run.trade_log || run.artifacts_trades_csv || []

    return (
        <div className="space-y-4 animate-fade-in">
            <Link to="/research/runs" className="inline-flex items-center gap-1 text-xs text-dark-400 hover:text-primary-300">
                <ArrowLeft className="h-3 w-3" /> All runs
            </Link>

            <HudPanel title={firstLine(run.prompt) || run.run_id} subtitle={`${run.run_id} · ${run.status}`}>
                {m ? (
                    <div className="grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-7">
                        <Stat label="Total return" value={pct(m.total_return)} tone={m.total_return >= 0 ? 'up' : 'down'} />
                        <Stat label="Annual" value={pct(m.annual_return)} tone={m.annual_return >= 0 ? 'up' : 'down'} />
                        <Stat label="Max drawdown" value={pct(m.max_drawdown)} tone="down" />
                        <Stat label="Sharpe" value={num(m.sharpe)} />
                        <Stat label="Win rate" value={pct(m.win_rate)} />
                        <Stat label="Trades" value={m.trade_count ?? '—'} />
                        <Stat label="Final value" value={num(m.final_value, 0)} />
                    </div>
                ) : (
                    <p className="text-sm text-dark-400">
                        {run.reason || 'This run produced no backtest, only a research answer.'}
                    </p>
                )}
            </HudPanel>

            {equity && (
                <HudPanel title="Equity curve" subtitle={equity.valueKey}>
                    <div className="h-72">
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={equity.data}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#6b7280' }} minTickGap={40} />
                                <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} domain={['auto', 'auto']} width={70} />
                                <Tooltip contentStyle={{ background: '#0b1220', border: '1px solid #1f2937', fontSize: 12 }} />
                                <Area type="monotone" dataKey="value" stroke="#38bdf8" fill="#38bdf8" fillOpacity={0.12} />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                </HudPanel>
            )}

            {trades.length > 0 && (
                <HudPanel title="Trades" subtitle={`${trades.length} rows`} padded={false}>
                    <div className="max-h-96 overflow-auto">
                        <table className="w-full text-xs">
                            <thead className="sticky top-0 bg-dark-950">
                                <tr className="hud-label text-left">
                                    {Object.keys(trades[0]).map((k) => <th key={k} className="px-3 py-2">{k}</th>)}
                                </tr>
                            </thead>
                            <tbody>
                                {trades.slice(0, 500).map((t, i) => (
                                    <tr key={i} className="border-t border-dark-800/70 font-mono">
                                        {Object.keys(trades[0]).map((k) => <td key={k} className="px-3 py-1.5">{String(t[k] ?? '')}</td>)}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </HudPanel>
            )}

            <CodePanel runId={runId} />

            {run.prompt && (
                <HudPanel title="Task">
                    <pre className="whitespace-pre-wrap font-mono text-xs text-dark-300">{run.prompt}</pre>
                </HudPanel>
            )}
        </div>
    )
}

function CodePanel({ runId }) {
    const [tab, setTab] = useState(null)
    const [code, setCode] = useState({})

    const open = async (kind) => {
        setTab(tab === kind ? null : kind)
        if (code[kind] !== undefined) return
        try {
            const res = kind === 'code' ? await researchApi.runCode(runId) : await researchApi.runPine(runId)
            const d = res.data
            const text = typeof d === 'string' ? d : d?.code || d?.source || d?.pine || (d && Object.keys(d).length ? JSON.stringify(d, null, 2) : null)
            setCode((c) => ({ ...c, [kind]: text || null }))
        } catch (e) {
            setCode((c) => ({ ...c, [kind]: e?.response?.status === 404 ? null : `Could not load: ${e.message}` }))
        }
    }

    return (
        <HudPanel
            title="Strategy code"
            right={
                <div className="flex gap-1">
                    <button onClick={() => open('code')} className={`btn-ghost !px-2 !py-1 !text-[10px] ${tab === 'code' ? '!text-primary-200' : ''}`}>
                        <Code2 className="mr-1 inline h-3 w-3" /> Python
                    </button>
                    <button onClick={() => open('pine')} className={`btn-ghost !px-2 !py-1 !text-[10px] ${tab === 'pine' ? '!text-primary-200' : ''}`}>
                        Pine (TradingView)
                    </button>
                </div>
            }
        >
            {!tab ? (
                <p className="text-xs text-dark-500">Show the code the agent wrote, or export it to TradingView Pine Script.</p>
            ) : code[tab] === undefined ? (
                <Loading rows={2} label="Loading code" />
            ) : code[tab] === null ? (
                <p className="text-xs text-dark-500">This run has no {tab === 'code' ? 'strategy code' : 'Pine export'}.</p>
            ) : (
                <pre className="max-h-[28rem] overflow-auto rounded border border-dark-800 bg-dark-950 p-3 font-mono text-[11px] text-dark-200">{code[tab]}</pre>
            )}
        </HudPanel>
    )
}

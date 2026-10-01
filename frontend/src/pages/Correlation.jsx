import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Grid3x3, Loader2, Play, X } from 'lucide-react'
import {
    Area, AreaChart, CartesianGrid, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import ResearchNav, { useEngine } from '../components/research/ResearchNav'
import { HudPanel, Empty, ErrorNote } from '../components/hud/HudPanel'
import { Chip, Field, Segmented } from '../components/research/Controls'
import { discoverApi, researchApi } from '../services/api'

/**
 * Correlation: how Tradeo's NSE instruments move together, and when the
 * market as a whole locks into one trade (a correlation regime, where
 * diversification quietly stops working).
 */

const DEFAULT = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'ITC', 'LT']

// Diverging scale: red for moving apart, cyan for moving together.
function cellColor(v) {
    if (v === null || v === undefined) return 'transparent'
    const a = Math.min(1, Math.abs(v))
    return v >= 0 ? `rgba(34, 211, 238, ${0.08 + a * 0.75})` : `rgba(248, 113, 113, ${0.08 + a * 0.75})`
}

export default function Correlation() {
    const [engine, reloadEngine] = useEngine()
    const navigate = useNavigate()
    const [universe, setUniverse] = useState([])
    const [symbols, setSymbols] = useState(DEFAULT)
    const [adding, setAdding] = useState('')
    const [days, setDays] = useState(250)
    const [method, setMethod] = useState('pearson')
    const [matrix, setMatrix] = useState(null)
    const [regime, setRegime] = useState(null)
    const [hover, setHover] = useState(null)
    const [pinned, setPinned] = useState(null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState(null)

    useEffect(() => {
        discoverApi
            .universe({ limit: 200 })
            .then((r) => setUniverse(r.data.instruments || []))
            .catch(() => {})
    }, [])

    const sectors = useMemo(() => {
        const by = {}
        for (const i of universe) if (i.asset_class === 'equity' && i.sector) (by[i.sector] ||= []).push(i.symbol)
        return Object.entries(by).filter(([, v]) => v.length >= 3).sort((a, b) => b[1].length - a[1].length)
    }, [universe])

    const run = async (list = symbols) => {
        if (list.length < 2) return setError('Pick at least two instruments.')
        setBusy(true)
        setError(null)
        setPinned(null)
        const codes = list.map((s) => `${s}.NS`).join(',')
        try {
            const [m, r] = await Promise.allSettled([
                researchApi.correlation(codes, days, method),
                researchApi.regime(codes, Math.min(365, Math.max(120, days))),
            ])
            if (m.status === 'fulfilled') setMatrix(m.value.data)
            else throw m.reason
            setRegime(r.status === 'fulfilled' ? r.value.data : null)
        } catch (e) {
            const d = e?.response?.data?.detail
            setError(Array.isArray(d) ? d.map((x) => x.msg).join('; ') : d || e.message)
        } finally {
            setBusy(false)
        }
    }

    useEffect(() => {
        if (engine?.online && !matrix) run()
    }, [engine?.online]) // eslint-disable-line react-hooks/exhaustive-deps

    const add = (sym) => {
        const s = sym.trim().toUpperCase().replace(/\.NS$/, '')
        if (s && !symbols.includes(s) && symbols.length < 30) setSymbols([...symbols, s])
        setAdding('')
    }

    const labels = (matrix?.labels || []).map((l) => l.replace(/\.NS$/, ''))
    const focus = pinned || hover
    const pairs = useMemo(() => {
        if (!matrix) return []
        const out = []
        matrix.matrix.forEach((row, i) =>
            row.forEach((v, j) => {
                if (j > i && v !== null) out.push({ a: labels[i], b: labels[j], v })
            }),
        )
        return out.sort((x, y) => y.v - x.v)
    }, [matrix]) // eslint-disable-line react-hooks/exhaustive-deps

    const regimeData = useMemo(
        () =>
            (regime?.dates || []).map((d, i) => ({
                date: d,
                density: regime.smoothed?.[i] ?? regime.density?.[i],
            })),
        [regime],
    )

    return (
        <div className="space-y-4 animate-fade-in">
            <ResearchNav
                title="Correlation"
                subtitle="How your instruments move together, and when the whole market locks into one trade and diversification stops working."
                engine={engine}
                onEngineChange={reloadEngine}
            />
            <ErrorNote error={error} onRetry={() => setError(null)} />

            <HudPanel>
                <div className="flex flex-wrap items-end gap-4">
                    <div className="min-w-[18rem] flex-1">
                        <span className="hud-label mb-1 block">Instruments (NSE)</span>
                        <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-dark-800 bg-dark-950/60 p-1.5">
                            {symbols.map((s) => (
                                <span key={s} className="flex items-center gap-1 rounded bg-primary-500/15 px-2 py-0.5 font-mono text-xs text-primary-200 animate-fade-in">
                                    {s}
                                    <button onClick={() => setSymbols(symbols.filter((x) => x !== s))} className="text-primary-400/60 hover:text-danger-300">
                                        <X className="h-3 w-3" />
                                    </button>
                                </span>
                            ))}
                            <input
                                list="corr-universe"
                                value={adding}
                                onChange={(e) => setAdding(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' || e.key === ',') {
                                        e.preventDefault()
                                        add(adding)
                                    } else if (e.key === 'Backspace' && !adding) setSymbols(symbols.slice(0, -1))
                                }}
                                placeholder="add symbol…"
                                className="min-w-[6rem] flex-1 bg-transparent px-1 font-mono text-xs text-dark-100 outline-none"
                            />
                            <datalist id="corr-universe">
                                {universe.map((i) => <option key={i.symbol} value={i.symbol}>{i.name}</option>)}
                            </datalist>
                        </div>
                    </div>
                    <Field label={`Window · ${days} days`}>
                        <input type="range" min={60} max={750} step={10} value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-40 accent-cyan-400" />
                    </Field>
                    <Field label="Method">
                        <Segmented value={method} onChange={setMethod} options={[{ id: 'pearson', label: 'Pearson' }, { id: 'spearman', label: 'Spearman' }]} />
                    </Field>
                    <button onClick={() => run()} disabled={busy || !engine?.online} className="btn-primary flex items-center gap-2 disabled:opacity-50">
                        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Compute
                    </button>
                </div>
                {sectors.length > 0 && (
                    <div className="mt-3 flex flex-wrap items-center gap-1.5">
                        <span className="hud-label mr-1">Presets</span>
                        {sectors.map(([sector, list]) => (
                            <Chip key={sector} onClick={() => { setSymbols(list.slice(0, 15)); run(list.slice(0, 15)) }}>
                                {sector} <span className="text-dark-500">{list.length}</span>
                            </Chip>
                        ))}
                    </div>
                )}
            </HudPanel>

            <div className="grid gap-4 xl:grid-cols-[1fr_20rem]">
                <HudPanel
                    title="Correlation matrix"
                    subtitle={matrix ? `${matrix.method} · ${matrix.window} days of daily returns · hover to trace, click to pin` : ''}
                >
                    {!matrix ? (
                        busy ? <div className="flex h-64 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-primary-400" /></div>
                            : <Empty icon={Grid3x3} title="Nothing computed yet" hint="Pick instruments and hit Compute." />
                    ) : (
                        <div className={`overflow-x-auto transition-opacity ${busy ? 'opacity-40' : ''}`} onMouseLeave={() => setHover(null)}>
                            <table className="border-separate" style={{ borderSpacing: 2 }}>
                                <thead>
                                    <tr>
                                        <th />
                                        {labels.map((l, j) => (
                                            <th key={l} className={`h-20 w-10 align-bottom font-mono text-[10px] font-normal transition-colors ${focus && (focus.j === j || focus.i === j) ? 'text-primary-200' : 'text-dark-500'}`}>
                                                <div className="w-10 origin-bottom-left translate-x-5 -rotate-45 whitespace-nowrap">{l}</div>
                                            </th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {matrix.matrix.map((row, i) => (
                                        <tr key={i}>
                                            <td className={`pr-2 text-right font-mono text-[10px] transition-colors ${focus && (focus.i === i || focus.j === i) ? 'text-primary-200' : 'text-dark-500'}`}>{labels[i]}</td>
                                            {row.map((v, j) => {
                                                const inFocus = focus && (focus.i === i || focus.j === j)
                                                const isFocus = focus && focus.i === i && focus.j === j
                                                return (
                                                    <td
                                                        key={j}
                                                        onMouseEnter={() => setHover({ i, j })}
                                                        onClick={() => setPinned(pinned?.i === i && pinned?.j === j ? null : { i, j })}
                                                        className={`h-10 w-10 cursor-pointer rounded-sm text-center font-mono text-[10px] transition-all duration-150 ${isFocus ? 'scale-110 ring-2 ring-primary-300' : ''} ${focus && !inFocus ? 'opacity-40' : ''}`}
                                                        style={{ background: cellColor(v), color: Math.abs(v) > 0.55 ? '#020617' : '#cbd5e1' }}
                                                    >
                                                        {v === null ? '' : v.toFixed(2)}
                                                    </td>
                                                )
                                            })}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </HudPanel>

                <div className="space-y-4">
                    {focus && matrix && (
                        <HudPanel title="Pair" glow>
                            <div className="font-mono text-sm text-dark-100">{labels[focus.i]} × {labels[focus.j]}</div>
                            <div className="mt-2 font-mono text-3xl" style={{ color: matrix.matrix[focus.i][focus.j] >= 0 ? '#67e8f9' : '#fca5a5' }}>
                                {matrix.matrix[focus.i][focus.j]?.toFixed(3)}
                            </div>
                            <p className="mt-2 text-xs text-dark-400">
                                {describe(matrix.matrix[focus.i][focus.j])}
                            </p>
                            {focus.i !== focus.j && (
                                <button
                                    onClick={() => navigate(`/research?q=${encodeURIComponent(`Backtest a pairs trade between ${labels[focus.i]}.NS and ${labels[focus.j]}.NS (correlation ${matrix.matrix[focus.i][focus.j]?.toFixed(2)} over ${days} days): z-score of the spread, enter at ±2, exit at 0.`)}`)}
                                    className="btn-ghost mt-3 flex w-full items-center justify-center gap-2 text-xs"
                                >
                                    <Bot className="h-3.5 w-3.5" /> Backtest as a pair trade
                                </button>
                            )}
                        </HudPanel>
                    )}
                    <HudPanel title="Most and least alike" padded={false}>
                        <div className="max-h-80 overflow-y-auto">
                            {[...pairs.slice(0, 5), ...pairs.slice(-5).reverse()].map((p, k) => (
                                <div key={`${p.a}${p.b}${k}`} className={`flex items-center justify-between px-4 py-1.5 text-xs ${k === 5 ? 'border-t border-dark-800' : ''}`}>
                                    <span className="font-mono text-dark-300">{p.a} · {p.b}</span>
                                    <span className="font-mono" style={{ color: p.v >= 0 ? '#67e8f9' : '#fca5a5' }}>{p.v.toFixed(2)}</span>
                                </div>
                            ))}
                        </div>
                    </HudPanel>
                </div>
            </div>

            {regimeData.length > 0 && (
                <HudPanel
                    title="Correlation regime"
                    subtitle="How tightly the whole basket moves together over time; shaded spans are lock-step episodes"
                >
                    <div className="h-56">
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={regimeData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#64748b' }} minTickGap={50} />
                                <YAxis tick={{ fontSize: 10, fill: '#64748b' }} domain={[0, 1]} width={36} />
                                <Tooltip contentStyle={{ background: '#020617', border: '1px solid #1e293b', fontSize: 12 }} formatter={(v) => [v === null ? '—' : Number(v).toFixed(3), 'density']} />
                                {(regime.episodes || []).map((e) => (
                                    <ReferenceArea key={e.start} x1={e.start} x2={e.end || regimeData[regimeData.length - 1].date} fill="#f59e0b" fillOpacity={0.12} />
                                ))}
                                <Area dataKey="density" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.12} connectNulls />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                    <p className="mt-2 text-xs text-dark-500">
                        {(regime.episodes || []).length} lock-step episode{(regime.episodes || []).length === 1 ? '' : 's'} in the last {regime.params?.days} days.
                    </p>
                </HudPanel>
            )}
        </div>
    )
}

function describe(v) {
    if (v === null || v === undefined) return ''
    if (v >= 0.99) return 'Same instrument.'
    if (v > 0.7) return 'Move almost as one. Holding both adds little diversification.'
    if (v > 0.4) return 'Clearly related. Some diversification benefit.'
    if (v > -0.2) return 'Largely independent. Good diversifiers for each other.'
    return 'Tend to move opposite. One can hedge the other.'
}

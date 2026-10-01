import { useCallback, useEffect, useRef, useState } from 'react'
import {
    Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart,
    ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Dices, Upload, Download, RefreshCw, Play, FileSpreadsheet, Info } from 'lucide-react'
import { flyBrainApi } from '../services/api'
import AnimatedValue from './hud/AnimatedValue'

/**
 * Fly brain test lab: Monte Carlo on any trade record, and custom CSV data
 * to test (and optionally train) the brain on. Lazy-loaded (recharts).
 */

const AXIS = { stroke: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }
const TOOLTIP = {
    contentStyle: {
        background: 'rgba(10,17,32,0.96)', border: '1px solid rgba(34,211,238,0.25)',
        borderRadius: 8, fontSize: 11, fontFamily: 'JetBrains Mono',
    },
    labelStyle: { color: '#67e8f9' },
}
const FLY = '#a78bfa'
const pct = (v, d = 1) => (v == null ? '–' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`)
const tone = (v) => (v > 0 ? 'text-success-400' : v < 0 ? 'text-danger-400' : 'text-white')

export default function FlyTestLab() {
    const [tab, setTab] = useState('mc')
    const [runs, setRuns] = useState([])
    const [mcSource, setMcSource] = useState('history')

    const loadRuns = useCallback(() => {
        flyBrainApi.csvRuns().then(({ data }) => setRuns(data)).catch(() => {})
    }, [])
    useEffect(loadRuns, [loadRuns])

    return (
        <div className="glass-card p-6 space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <Dices className="w-6 h-6 text-primary-400" />
                    <div>
                        <h3 className="text-lg font-semibold text-white">Fly Brain · Test Lab</h3>
                        <p className="text-xs text-dark-400">
                            Monte Carlo on its trades, and your own CSV data to test and train it on.
                        </p>
                    </div>
                </div>
                <div className="flex gap-2">
                    <button onClick={() => setTab('mc')} className={tab === 'mc' ? 'btn-primary' : 'btn-secondary'}>
                        Monte Carlo
                    </button>
                    <button onClick={() => setTab('csv')} className={tab === 'csv' ? 'btn-primary' : 'btn-secondary'}>
                        Custom CSV
                    </button>
                </div>
            </div>

            {tab === 'mc' ? (
                <MonteCarlo runs={runs} source={mcSource} setSource={setMcSource} />
            ) : (
                <CsvLab runs={runs} reloadRuns={loadRuns}
                    onMonteCarlo={(id) => { setMcSource(`csv:${id}`); setTab('mc') }} />
            )}
        </div>
    )
}

/* ------------------------------------------------------------------ */

function MonteCarlo({ runs, source, setSource }) {
    const [fraction, setFraction] = useState(5)
    const [result, setResult] = useState(null)
    const [busy, setBusy] = useState(false)

    const run = useCallback(async () => {
        setBusy(true)
        try {
            const { data } = await flyBrainApi.monteCarlo(source, fraction)
            setResult(data)
        } catch (err) {
            setResult({ ok: false, error: err?.response?.data?.detail || err.message })
        } finally {
            setBusy(false)
        }
    }, [source, fraction])

    useEffect(() => { run() }, [source]) // eslint-disable-line react-hooks/exhaustive-deps

    const s = result?.summary
    return (
        <div className="space-y-5">
            <div className="flex flex-wrap items-end gap-3">
                <label className="text-xs text-dark-400">
                    Trades to resample
                    <select className="input-field mt-1" value={source} onChange={(e) => setSource(e.target.value)}>
                        <option value="history">History replay (NSE 2018→today)</option>
                        <option value="live">Live paper trades</option>
                        {runs.map((r) => (
                            <option key={r.id} value={`csv:${r.id}`}>CSV: {r.filename} ({r.trades} trades)</option>
                        ))}
                    </select>
                </label>
                <label className="text-xs text-dark-400">
                    Equity per trade: <b className="text-white">{fraction}%</b>
                    <input type="range" min="1" max="35" step="0.5" value={fraction}
                        onChange={(e) => setFraction(Number(e.target.value))} className="block w-48 mt-2" />
                </label>
                <button onClick={run} disabled={busy} className="btn-primary flex items-center gap-2">
                    {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                    Run 2,000 simulations
                </button>
            </div>

            {result && !result.ok && <p className="text-sm text-alert-300">{result.error}</p>}

            {result?.ok && (
                <>
                    <p className="text-xs text-dark-400">
                        {result.paths.toLocaleString()} paths × {result.trades_per_path} trades, resampled from{' '}
                        {result.sample.trades.toLocaleString()} real trades (avg {pct(result.sample.avg_trade_pct, 3)},
                        win rate {result.sample.win_rate}%, best {pct(result.sample.best_pct)}, worst {pct(result.sample.worst_pct)}),
                        each risking {result.fraction_pct}% of equity.
                    </p>

                    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                        <Stat label="Chance of profit" value={`${s.prob_profit}%`} className={s.prob_profit >= 50 ? 'text-success-400' : 'text-danger-400'} />
                        <Stat label="Median outcome" value={pct(s.median_pct)} className={tone(s.median_pct)} />
                        <Stat label="Bad case (5th pct)" value={pct(s.p5_pct)} className={tone(s.p5_pct)} />
                        <Stat label="Good case (95th pct)" value={pct(s.p95_pct)} className={tone(s.p95_pct)} />
                        <Stat label="Max drawdown (median / 95th)" value={`${s.median_max_dd_pct}% / ${s.p95_max_dd_pct}%`} />
                    </div>

                    <div className="rounded-lg border border-primary-400/25 bg-primary-500/5 p-3 text-sm text-dark-200">
                        <b className="text-white">To improve the trade output: </b>{result.advice}
                    </div>

                    <div className="grid lg:grid-cols-2 gap-5">
                        <Chart title="Fan chart: equity across 2,000 possible futures"
                            note="Shaded: 5–95th and 25–75th percentile. Thin lines: 15 individual paths.">
                            <ComposedChart data={result.fan}>
                                <CartesianGrid stroke="#1e293b" vertical={false} />
                                <XAxis dataKey="trade" {...AXIS} label={{ value: 'trades', position: 'insideBottomRight', offset: -2, fill: '#64748b', fontSize: 10 }} />
                                <YAxis {...AXIS} domain={['auto', 'auto']} tickFormatter={(v) => `₹${v.toFixed(2)}`} />
                                <Tooltip {...TOOLTIP} formatter={(v, n) => [`₹${Number(v).toFixed(3)}`, n]} />
                                <Area dataKey={(d) => [d.p5, d.p95]} name="5–95%" fill={FLY} fillOpacity={0.12} stroke="none" isAnimationActive={false} />
                                <Area dataKey={(d) => [d.p25, d.p75]} name="25–75%" fill={FLY} fillOpacity={0.25} stroke="none" isAnimationActive={false} />
                                {Array.from({ length: 15 }, (_, i) => (
                                    <Line key={i} dataKey={`s${i}`} stroke="#64748b" strokeOpacity={0.35} dot={false} strokeWidth={0.8} isAnimationActive={false} legendType="none" name={`path ${i + 1}`} />
                                ))}
                                <Line dataKey="median" name="Median" stroke={FLY} strokeWidth={2.2} dot={false} isAnimationActive={false} />
                                <ReferenceLine y={1} stroke="#94a3b8" strokeDasharray="4 4" />
                            </ComposedChart>
                        </Chart>

                        <Chart title="Where the 2,000 paths ended" note="Final return of each simulated path.">
                            <BarChart data={result.histogram}>
                                <CartesianGrid stroke="#1e293b" vertical={false} />
                                <XAxis dataKey="from_pct" {...AXIS} tickFormatter={(v) => `${v}%`} />
                                <YAxis {...AXIS} />
                                <Tooltip {...TOOLTIP} labelFormatter={(v, p) => p?.[0] ? `${p[0].payload.from_pct}% to ${p[0].payload.to_pct}%` : v} />
                                <ReferenceLine x={result.histogram.reduce((a, b) => (Math.abs(b.from_pct) < Math.abs(a.from_pct) ? b : a)).from_pct} stroke="#94a3b8" strokeDasharray="4 4" />
                                <Bar dataKey="paths" fill={FLY} isAnimationActive={false} />
                            </BarChart>
                        </Chart>
                    </div>

                    <div className="grid lg:grid-cols-2 gap-5">
                        <Table title="Position size sweep"
                            note={`Recommended: best median growth with a 95th-percentile drawdown under 20%.`}
                            head={['Per trade', 'Median', 'Bad (5%)', 'P(profit)', 'DD 95th']}
                            rows={result.sizing.map((r) => [
                                <span key="f" className={r.fraction_pct === result.recommended_fraction_pct ? 'text-success-400 font-bold' : ''}>
                                    {r.fraction_pct}%{r.fraction_pct === result.recommended_fraction_pct ? ' ★' : ''}
                                </span>,
                                <span key="m" className={tone(r.median_pct)}>{pct(r.median_pct)}</span>,
                                <span key="b" className={tone(r.p5_pct)}>{pct(r.p5_pct)}</span>,
                                `${r.prob_profit}%`,
                                <span key="d" className={r.p95_max_dd_pct > 20 ? 'text-danger-400' : ''}>{r.p95_max_dd_pct}%</span>,
                            ])} />
                        {result.selectivity.length > 0 && (
                            <Table title="Being pickier: keep only its highest-valued trades"
                                note="Uses the value the brain predicted when it chose. Scored on the same trades, so optimistic."
                                head={['Keep', 'Trades', 'Avg', 'Win', 'Median']}
                                rows={result.selectivity.map((r) => [
                                    r.keep, r.trades,
                                    <span key="a" className={tone(r.avg_trade_pct)}>{pct(r.avg_trade_pct, 3)}</span>,
                                    `${r.win_rate}%`,
                                    <span key="m" className={tone(r.median_pct)}>{pct(r.median_pct)}</span>,
                                ])} />
                        )}
                    </div>
                    <p className="text-xs text-dark-500 flex gap-1.5"><Info className="w-3.5 h-3.5 shrink-0" />{result.caveat}</p>
                </>
            )}
        </div>
    )
}

/* ------------------------------------------------------------------ */

function CsvLab({ runs, reloadRuns, onMonteCarlo }) {
    const [file, setFile] = useState(null)
    const [startFrom, setStartFrom] = useState('live')
    const [trainLive, setTrainLive] = useState(false)
    const [job, setJob] = useState(null)
    const [error, setError] = useState(null)
    const [result, setResult] = useState(null)
    const input = useRef(null)

    const open = useCallback(async (id) => {
        const { data } = await flyBrainApi.csvRun(id)
        setResult(data)
    }, [])

    // Poll the job until the run is saved.
    useEffect(() => {
        if (!job) return
        const id = setInterval(async () => {
            try {
                const { data } = await flyBrainApi.csvJob(job)
                if (data.running) return
                clearInterval(id)
                setJob(null)
                if (data.error) setError(data.error)
                else {
                    await open(data.run_id)
                    reloadRuns()
                }
            } catch (err) {
                clearInterval(id)
                setJob(null)
                setError(err.message)
            }
        }, 1500)
        return () => clearInterval(id)
    }, [job, open, reloadRuns])

    const run = async () => {
        if (!file) return
        setError(null)
        setResult(null)
        try {
            const { data } = await flyBrainApi.runCsv(file, startFrom, trainLive)
            setJob(data.job)
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        }
    }

    const s = result?.summary
    return (
        <div className="space-y-5">
            {/* How to make the CSV */}
            <div className="rounded-lg border border-dark-600/60 bg-dark-800/40 p-4 text-sm text-dark-300 space-y-2">
                <div className="flex items-center justify-between gap-3">
                    <b className="text-white flex items-center gap-2"><FileSpreadsheet className="w-4 h-4" /> How to create the CSV</b>
                    <a href={flyBrainApi.csvSampleUrl()} className="btn-secondary flex items-center gap-2 text-xs" download>
                        <Download className="w-3.5 h-3.5" /> Download a sample (RELIANCE + TCS, 5 years)
                    </a>
                </div>
                <ol className="list-decimal pl-5 space-y-1 text-xs">
                    <li>
                        One row per stock per trading day, with a header row:
                        <code className="ml-1 text-primary-300">date,symbol,open,high,low,close,volume</code>
                    </li>
                    <li>
                        <b>date</b> as <code>2024-03-15</code> (YYYY-MM-DD). <b>symbol</b> is optional: leave it out for a single stock and
                        the file name is used (<code>INFY.csv</code> → INFY). Common names also work: Date/Open/High/Low/Close/Adj Close/Volume, Ticker.
                    </li>
                    <li>
                        <b>Daily bars only</b>, at least <b>320 trading days per stock</b> (about 15 months). The first ~250 days warm up the
                        200-day average and 52-week range; trading starts after that. 3–10 years is better.
                    </li>
                    <li>
                        One stock works (features are standardised over time); <b>5 or more</b> lets the brain rank stocks against each
                        other each day, like the live system. Up to 25 MB.
                    </li>
                    <li>
                        Easy sources: NSE's historical data page (Equity → Historical data, download CSV), Yahoo Finance
                        (Historical Data → Download), or yfinance in Python:
                        <code className="block mt-1 text-primary-300">
                            yf.download("INFY.NS", period="10y").reset_index().to_csv("INFY.csv", index=False)
                        </code>
                    </li>
                    <li>
                        Rules applied: decide at the close, buy the next open, hold 5 days, 0.42% round-trip Indian costs, up to 5 slots,
                        cash when it expects a loss.
                    </li>
                </ol>
            </div>

            {/* Run */}
            <div className="flex flex-wrap items-end gap-3">
                <div>
                    <input ref={input} type="file" accept=".csv,text/csv" className="hidden"
                        onChange={(e) => setFile(e.target.files?.[0] || null)} />
                    <button onClick={() => input.current?.click()} className="btn-secondary flex items-center gap-2">
                        <Upload className="w-4 h-4" /> {file ? file.name : 'Choose CSV…'}
                    </button>
                </div>
                <label className="text-xs text-dark-400">
                    Brain
                    <select className="input-field mt-1" value={startFrom} onChange={(e) => setStartFrom(e.target.value)}>
                        <option value="live">Start from the live brain (already trained on NSE)</option>
                        <option value="fresh">Fresh brain (learns only from this file)</option>
                    </select>
                </label>
                <label className="flex items-center gap-2 text-xs text-dark-300 pb-2">
                    <input type="checkbox" checked={trainLive} onChange={(e) => setTrainLive(e.target.checked)} />
                    Keep what it learns as the live brain (training)
                </label>
                <button onClick={run} disabled={!file || !!job} className="btn-primary flex items-center gap-2">
                    {job ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                    {job ? 'Running…' : 'Test on this data'}
                </button>
            </div>
            {trainLive && (
                <p className="text-xs text-alert-300">
                    Training changes the brain that paper trades live. Use it with data it hasn't seen: if the file overlaps the NSE
                    history (2017→today, 66 large caps), it re-learns the same trades twice.
                </p>
            )}
            {error && <p className="text-sm text-danger-400">{error}</p>}

            {/* Result */}
            {result && (
                <div className="space-y-4">
                    <p className="text-xs text-dark-400">
                        <b className="text-white">{result.filename}</b>: {result.data.symbols.length} stock(s) ({result.data.symbols.slice(0, 8).join(', ')}
                        {result.data.symbols.length > 8 ? '…' : ''}), {result.data.rows.toLocaleString()} rows, {result.data.from} → {result.data.to},
                        trading from {result.data.trading_from}; features standardised {result.data.standardised}; {result.data.slots} slot(s).
                        Brain: {result.brain.start_from}, learned {result.brain.learned_rewards} rewards / {result.brain.learned_punishments} punishments
                        {result.brain.kept_as_live_brain ? ' — kept as the live brain.' : ' — live brain unchanged.'}
                    </p>
                    {result.notes?.length > 0 && <p className="text-xs text-alert-300">{result.notes.join(' · ')}</p>}

                    <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                        <Stat label="Trades" value={s.trades} />
                        <Stat label="Won" value={s.wins} className="text-success-400" />
                        <Stat label="Lost" value={s.losses} className="text-danger-400" />
                        <Stat label="Win rate" value={`${s.win_rate}%`} />
                        <Stat label="Fly brain / year" value={pct(s.per_year_pct)} className={tone(s.per_year_pct)} />
                        <Stat label="Buy & hold (total)" value={pct(s.buy_and_hold_pct)} className={tone(s.buy_and_hold_pct)} />
                    </div>
                    <p className="text-xs text-dark-400">
                        Total {pct(s.total_return_pct)} · avg trade {pct(s.avg_trade_pct, 3)} after {s.round_trip_cost_pct}% costs ·
                        invested {s.invested_pct}% of the time · rotating everything every 5 days: {pct(s.rotate_everything_per_year_pct)}/yr
                    </p>

                    <Chart title="Growth of ₹1 on your data">
                        <LineChart data={result.equity}>
                            <CartesianGrid stroke="#1e293b" vertical={false} />
                            <XAxis dataKey="date" {...AXIS} minTickGap={50} />
                            <YAxis {...AXIS} domain={['auto', 'auto']} tickFormatter={(v) => `₹${v.toFixed(2)}`} />
                            <Tooltip {...TOOLTIP} />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine y={1} stroke="#475569" strokeDasharray="4 4" />
                            <Line dataKey="fly_brain" name="Fly brain (RL)" stroke={FLY} strokeWidth={2} dot={false} isAnimationActive={false} />
                            <Line dataKey="buy_and_hold" name="Buy & hold" stroke="#22c55e" dot={false} isAnimationActive={false} />
                            <Line dataKey="rotate_everything" name="Rotate everything" stroke="#f59e0b" dot={false} isAnimationActive={false} />
                        </LineChart>
                    </Chart>

                    {result.monte_carlo?.ok && (
                        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-primary-400/25 bg-primary-500/5 p-3 text-sm">
                            <span className="text-dark-200">
                                Monte Carlo (5% per trade): <b className="text-white">{result.monte_carlo.summary.prob_profit}%</b> chance of
                                profit, median {pct(result.monte_carlo.summary.median_pct)}.
                            </span>
                            <button onClick={() => onMonteCarlo(result.id)} className="btn-secondary text-xs">Open full Monte Carlo</button>
                        </div>
                    )}

                    <div className="grid md:grid-cols-2 gap-5">
                        <Table title="By stock" head={['Stock', 'Trades', 'Avg', 'Win rate']}
                            rows={result.by_symbol.slice(0, 15).map((r) => [
                                r.symbol, r.trades, <span key="a" className={tone(r.avg_pct)}>{pct(r.avg_pct, 2)}</span>, `${r.win_rate}%`,
                            ])} />
                        <Table title="Latest trades" head={['Date', 'Stock', 'Expected', 'Result']}
                            rows={result.recent_trades.slice(0, 15).map((t) => [
                                t.date, t.symbol, pct(t.expected_pct, 2),
                                <span key="r" className={tone(t.net_pct)}>{pct(t.net_pct, 2)}</span>,
                            ])} />
                    </div>
                </div>
            )}

            {/* Past runs */}
            {runs.length > 0 && (
                <Table title="Past CSV runs" head={['File', 'Stocks', 'Trades', 'Win rate', '/ year', '']}
                    rows={runs.map((r) => [
                        <button key="f" onClick={() => open(r.id)} className="text-primary-300 hover:underline text-left">
                            {r.filename}
                        </button>,
                        r.symbols, r.trades, `${r.win_rate}%`,
                        <span key="y" className={tone(r.per_year_pct)}>{pct(r.per_year_pct)}</span>,
                        r.kept_as_live_brain ? <span key="k" className="badge badge-alert">trained live</span> : '',
                    ])} />
            )}
        </div>
    )
}

/* ------------------------------------------------------------------ */

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

function Stat({ label, value, className = 'text-white' }) {
    return (
        <div className="stat-card">
            <p className="text-xs text-dark-400">{label}</p>
            <p className={`text-lg font-bold ${className}`}><AnimatedValue value={value} /></p>
        </div>
    )
}

function Table({ title, note, head, rows }) {
    return (
        <div>
            <h4 className="text-sm font-semibold text-white">{title}</h4>
            {note && <p className="text-xs text-dark-400 mb-1">{note}</p>}
            <table className="w-full text-xs mt-1">
                <thead>
                    <tr className="border-b border-dark-700 text-dark-400">
                        {head.map((h, i) => <th key={i} className={`p-1.5 ${i ? 'text-right' : 'text-left'}`}>{h}</th>)}
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

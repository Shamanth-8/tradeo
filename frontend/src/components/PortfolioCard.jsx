import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { Info, Plug, X } from 'lucide-react'
import { HudPanel, Stat, Delta, Loading } from './hud/HudPanel'
import { wealthApi } from '../services/api'
import AnimatedValue from './hud/AnimatedValue'

/**
 * Portfolio, with nothing anonymous about it.
 *
 *   Paper — the automated paper account. Only Tradeo's agents trade it; every
 *           position says which agent opened it, when, and why.
 *   Real  — your actual accounts: connected brokers, imported CDSL/NSDL
 *           statements, manual entries — each listed as its own source.
 *
 * Opens on Real when a real source exists, otherwise Paper. The choice is
 * remembered.
 */

const VIEW_KEY = 'tradeo.portfolio.view'

const rupees = (v) => `₹${Number(v || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
const signed = (v) => `${v >= 0 ? '+' : '−'}${rupees(Math.abs(v))}`
const tone = (v) => (v > 0 ? 'text-success-400' : v < 0 ? 'text-danger-400' : 'text-dark-200')

function readView() {
    try {
        return localStorage.getItem(VIEW_KEY)
    } catch {
        return null
    }
}

export default function PortfolioCard() {
    const navigate = useNavigate()
    const [data, setData] = useState(null)
    const [view, setView] = useState(readView)
    const [details, setDetails] = useState(false)

    const load = useCallback(() => {
        wealthApi.overview().then(({ data }) => setData(data)).catch(() => {})
    }, [])

    useEffect(() => {
        load()
        const timer = setInterval(load, 60000)
        return () => clearInterval(timer)
    }, [load])

    const choose = (next) => {
        setView(next)
        try {
            localStorage.setItem(VIEW_KEY, next)
        } catch {
            /* private mode: not remembered, still works */
        }
    }

    const active = view || data?.default_view || 'paper'

    const tabs = (
        <div className="flex rounded border border-dark-700/70 p-0.5 text-[10px] font-mono uppercase">
            {['paper', 'real'].map((v) => (
                <button key={v} onClick={() => choose(v)}
                    className={`px-2 py-0.5 rounded ${active === v ? 'bg-primary-500/20 text-primary-300' : 'text-dark-400 hover:text-dark-200'}`}>
                    {v === 'paper' ? 'Paper' : 'Real'}
                </button>
            ))}
        </div>
    )

    return (
        <>
            <HudPanel title="Portfolio" corners={false} right={tabs}>
                {!data ? (
                    <Loading rows={2} label="Portfolio" />
                ) : active === 'paper' ? (
                    <PaperSummary paper={data.paper} onDetails={() => setDetails(true)} />
                ) : (
                    <RealSummary real={data.real} onDetails={() => setDetails(true)}
                        onConnect={() => navigate('/setup?section=brokers')} />
                )}
            </HudPanel>
            {/* A portal: the side column's backdrop blur would otherwise trap
                this "fixed" overlay inside the column and clip it. */}
            {details && data && createPortal(
                <Details data={data} view={active} setView={choose} onClose={() => setDetails(false)}
                    onConnect={() => navigate('/setup?section=brokers')} />,
                document.body,
            )}
        </>
    )
}

/* ------------------------------------------------------------------ */

function PaperSummary({ paper, onDetails }) {
    const a = paper.account
    return (
        <div className="space-y-3">
            <Stat label="Paper account value" value={rupees(a.equity)}
                sub={`${paper.positions.length} open position${paper.positions.length === 1 ? '' : 's'} · cash ${rupees(a.cash)}`} />
            <div className="flex items-center justify-between">
                <span className="hud-label">Since start ({rupees(a.starting_capital)})</span>
                <Delta value={a.total_return_percent} />
            </div>
            {paper.positions.slice(0, 3).map((p) => (
                <div key={p.symbol} className="flex items-center justify-between text-[11px]">
                    <span className="text-dark-200">{p.symbol} <span className="text-dark-500">· {p.agent.name}</span></span>
                    <span className={tone(p.pnl)}>{signed(p.pnl)}</span>
                </div>
            ))}
            <button onClick={onDetails} className="flex items-center gap-1.5 text-[11px] text-primary-300 hover:underline">
                <Info className="h-3 w-3" /> Where this comes from · all trades
            </button>
        </div>
    )
}

function RealSummary({ real, onDetails, onConnect }) {
    if (!real.available) {
        return (
            <div className="space-y-3 text-[12px]">
                <p className="text-dark-300">No real account connected — nothing to show.</p>
                <p className="text-dark-500">
                    Supported: {real.supported_brokers.join(', ')}. Or import a CDSL/NSDL statement on the Wealth page.
                </p>
                <button onClick={onConnect} className="btn-primary flex items-center gap-2 text-xs">
                    <Plug className="h-3.5 w-3.5" /> Connect a broker
                </button>
            </div>
        )
    }
    const t = real.totals
    return (
        <div className="space-y-3">
            <Stat label="Real portfolio value" value={rupees(t.value)}
                sub={real.sources.map((s) => s.label).join(' + ')} />
            <div className="flex items-center justify-between">
                <span className="hud-label">Unrealised</span>
                <Delta value={t.pnl_percent} />
            </div>
            <button onClick={onDetails} className="flex items-center gap-1.5 text-[11px] text-primary-300 hover:underline">
                <Info className="h-3 w-3" /> Each account and holding
            </button>
        </div>
    )
}

/* ------------------------------------------------------------------ */

function Details({ data, view, setView, onClose, onConnect }) {
    const { paper, real } = data
    return (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/80 p-4 backdrop-blur-sm" onClick={onClose}>
            <div className="w-full max-w-4xl space-y-5 rounded-xl border border-primary-400/20 bg-dark-900 p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
                <div className="flex items-center justify-between">
                    <div className="flex gap-2">
                        <button onClick={() => setView('paper')} className={view === 'paper' ? 'btn-primary' : 'btn-secondary'}>Paper account</button>
                        <button onClick={() => setView('real')} className={view === 'real' ? 'btn-primary' : 'btn-secondary'}>Real accounts</button>
                    </div>
                    <button onClick={onClose} className="btn-ghost"><X className="h-4 w-4" /></button>
                </div>

                {view === 'paper' ? <PaperDetails paper={paper} /> : <RealDetails real={real} onConnect={onConnect} />}
            </div>
        </div>
    )
}

export function PaperDetails({ paper }) {
    const a = paper.account
    return (
        <div className="space-y-5 text-sm">
            <div>
                <h3 className="text-lg font-semibold text-white">{paper.label}</h3>
                <p className="text-xs text-dark-400">{paper.explanation}</p>
            </div>
            <div className="stagger grid grid-cols-2 gap-3 md:grid-cols-4">
                <Box label="Value now" value={rupees(a.equity)} />
                <Box label="Cash" value={rupees(a.cash)} />
                <Box label="In positions" value={rupees(a.market_value)} />
                <Box label="Since start" value={`${signed(a.total_return)} (${a.total_return_percent}%)`} className={tone(a.total_return)} />
            </div>

            <Section title="Who traded — by agent">
                {paper.by_agent.length === 0 ? <Muted>No trades yet.</Muted> : (
                    <Table head={['Agent', 'How it picks', 'Trades', 'Open', 'Won', 'Lost', 'Realised']}
                        rows={paper.by_agent.map((r) => [
                            r.agent.name, <span key="h" className="text-dark-400">{r.agent.how}</span>, r.trades, r.open, r.wins, r.losses,
                            <span key="p" className={tone(r.realised_pnl)}>{signed(r.realised_pnl)}</span>,
                        ])} />
                )}
            </Section>

            <Section title="Open positions">
                {paper.positions.length === 0 ? <Muted>None open.</Muted> : (
                    <div className="space-y-2">
                        {paper.positions.map((p) => (
                            <div key={p.symbol} className="rounded bg-dark-800/50 p-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <span className="font-semibold text-white">
                                        {p.symbol} <span className="font-normal text-dark-400">× {p.quantity} @ {rupees(p.avg_price)} → now {rupees(p.ltp)}</span>
                                    </span>
                                    <span className={tone(p.pnl)}>{signed(p.pnl)} ({p.pnl_percent}%)</span>
                                </div>
                                <div className="mt-1 text-xs text-dark-400">
                                    Opened by <b className="text-primary-300">{p.agent.name}</b> on {p.opened_ist} IST
                                    {p.stop ? ` · stop ${rupees(p.stop)}` : ''}{p.target ? ` · target ${rupees(p.target)}` : ''}
                                </div>
                                {p.reason && <div className="mt-1 text-xs text-dark-500">Why: {p.reason}</div>}
                            </div>
                        ))}
                    </div>
                )}
            </Section>

            <Section title="Closed trades">
                {paper.closed_trades.length === 0 ? <Muted>No closed trades yet.</Muted> : (
                    <Table head={['Closed (IST)', 'Stock', 'Agent', 'Qty', 'Entry → Exit', 'Result', 'P&L']}
                        rows={paper.closed_trades.map((t) => [
                            t.closed_ist, t.symbol, t.agent.name, t.quantity,
                            `${rupees(t.entry)} → ${rupees(t.exit)}`, String(t.result).replace('_', ' '),
                            <span key="p" className={tone(t.pnl)}>{signed(t.pnl)}</span>,
                        ])} />
                )}
            </Section>
        </div>
    )
}

function RealDetails({ real, onConnect }) {
    return (
        <div className="space-y-5 text-sm">
            <div>
                <h3 className="text-lg font-semibold text-white">{real.label}</h3>
                <p className="text-xs text-dark-400">{real.explanation}</p>
            </div>
            {!real.available ? (
                <div className="space-y-3">
                    <Muted>
                        No real account is connected or imported, so there is nothing here — no sample data is shown.
                        Supported brokers: {real.supported_brokers.join(', ')}.
                    </Muted>
                    <button onClick={onConnect} className="btn-primary flex items-center gap-2 text-xs">
                        <Plug className="h-3.5 w-3.5" /> Connect a broker
                    </button>
                </div>
            ) : (
                real.sources.map((s) => (
                    <Section key={s.id} title={`${s.label} · ${s.kind === 'broker' ? 'broker account' : s.kind === 'statement' ? 'imported statement' : 'entered by hand'}`}>
                        <p className="mb-2 text-xs text-dark-400">
                            {s.instruments} holdings · invested {rupees(s.invested)} · value {rupees(s.value)} ·{' '}
                            <span className={tone(s.pnl)}>{signed(s.pnl)}</span>
                        </p>
                        {s.holdings.length > 0 && (
                            <Table head={['Stock', 'Qty', 'Avg', 'Now', 'Value', 'P&L']}
                                rows={s.holdings.map((h) => [
                                    h.symbol, h.quantity, rupees(h.avg_price), rupees(h.ltp), rupees(h.current_value),
                                    <span key="p" className={tone(h.pnl)}>{signed(h.pnl)} ({h.pnl_percent}%)</span>,
                                ])} />
                        )}
                    </Section>
                ))
            )}
            {real.errors?.length > 0 && <p className="text-xs text-alert-300">{real.errors.join(' · ')}</p>}
        </div>
    )
}

/* ------------------------------------------------------------------ */

function Section({ title, children }) {
    return (
        <div>
            <h4 className="mb-2 text-sm font-semibold text-white">{title}</h4>
            {children}
        </div>
    )
}

function Box({ label, value, className = 'text-white' }) {
    return (
        <div className="stat-card">
            <p className="text-xs text-dark-400">{label}</p>
            <p className={`text-base font-bold ${className}`}><AnimatedValue value={value} /></p>
        </div>
    )
}

function Muted({ children }) {
    return <p className="text-xs text-dark-400">{children}</p>
}

function Table({ head, rows }) {
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-xs">
                <thead>
                    <tr className="border-b border-dark-700 text-dark-400">
                        {head.map((h) => <th key={h} className="p-1.5 text-left">{h}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((cells, i) => (
                        <tr key={i} className="border-b border-dark-800/60">
                            {cells.map((c, j) => <td key={j} className="p-1.5 text-dark-200">{c}</td>)}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}

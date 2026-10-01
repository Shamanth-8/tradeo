import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Loader2, Minus, Plus, Trash2 } from 'lucide-react'
import {
    CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import ResearchNav, { Banner, useEngine } from '../components/research/ResearchNav'
import { HudPanel, Stat, ErrorNote } from '../components/hud/HudPanel'
import { Field, Segmented } from '../components/research/Controls'
import { optionsApi, researchApi } from '../services/api'

/**
 * Options Lab: build an NSE option strategy and see its payoff and Greeks.
 *
 * Tradeo supplies the instruments (underlyings, expiries, strikes, lot sizes)
 * from the public scrip master and the spot; the research engine does the
 * maths (Black-Scholes marks, scenario grid). Live premiums need a broker, so
 * a blank premium is priced from the volatility you set, and says so.
 */

const TEMPLATES = {
    'Long call': [{ type: 'call', off: 0, qty: 1 }],
    'Long put': [{ type: 'put', off: 0, qty: 1 }],
    'Bull call spread': [{ type: 'call', off: 0, qty: 1 }, { type: 'call', off: 4, qty: -1 }],
    'Bear put spread': [{ type: 'put', off: 0, qty: 1 }, { type: 'put', off: -4, qty: -1 }],
    'Long straddle': [{ type: 'call', off: 0, qty: 1 }, { type: 'put', off: 0, qty: 1 }],
    'Short strangle': [{ type: 'call', off: 4, qty: -1 }, { type: 'put', off: -4, qty: -1 }],
    'Iron condor': [
        { type: 'put', off: -8, qty: 1 }, { type: 'put', off: -4, qty: -1 },
        { type: 'call', off: 4, qty: -1 }, { type: 'call', off: 8, qty: 1 },
    ],
    'Covered-call style': [{ type: 'call', off: 3, qty: -1 }],
}

const inr = (v) =>
    v === null || v === undefined ? 'unlimited' : `₹${Math.round(v).toLocaleString('en-IN')}`

function daysTo(expiry) {
    const ms = new Date(`${expiry}T15:30:00+05:30`) - Date.now()
    return Math.max(0.1, ms / 86400000)
}

export default function OptionsLab() {
    const [engine, reloadEngine] = useEngine()
    const navigate = useNavigate()
    const [underlyings, setUnderlyings] = useState([])
    const [underlying, setUnderlying] = useState('NIFTY')
    const [expiries, setExpiries] = useState([])
    const [expiry, setExpiry] = useState('')
    const [chain, setChain] = useState(null)
    const [spot, setSpot] = useState(null)
    const [legs, setLegs] = useState([])
    const [iv, setIv] = useState(14)
    const [rate] = useState(6.5)
    const [daysLeft, setDaysLeft] = useState(null)
    const [result, setResult] = useState(null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState(null)
    const timer = useRef(null)

    useEffect(() => {
        optionsApi.underlyings().then((r) => setUnderlyings(r.data.underlyings || [])).catch(() => {})
    }, [])

    // Underlying → expiries and spot.
    useEffect(() => {
        setChain(null)
        setSpot(null)
        setLegs([])
        setResult(null)
        optionsApi
            .expiries(underlying)
            .then((r) => {
                setExpiries(r.data.expiries || [])
                setExpiry(r.data.nearest_usable || r.data.expiries?.[0] || '')
            })
            .catch((e) => setError(e?.response?.data?.detail || e.message))
        optionsApi
            .spot(underlying)
            .then((r) => setSpot(r.data.spot))
            .catch(() => setError(`No spot price for ${underlying}; type one in.`))
    }, [underlying])

    // Expiry → strikes and lot size.
    useEffect(() => {
        if (!expiry) return
        setDaysLeft(Math.round(daysTo(expiry) * 10) / 10)
        optionsApi.chain(underlying, expiry).then((r) => setChain(r.data)).catch((e) => setError(e?.response?.data?.detail || e.message))
    }, [underlying, expiry])

    const strikes = useMemo(
        () => [...new Set((chain?.contracts || []).map((c) => c.strike))].sort((a, b) => a - b),
        [chain],
    )
    const lot = chain?.contracts?.[0]?.lot_size || 1
    const step = chain?.strike_step || 50
    const atm = useMemo(() => {
        if (!strikes.length || !spot) return null
        return strikes.reduce((best, k) => (Math.abs(k - spot) < Math.abs(best - spot) ? k : best), strikes[0])
    }, [strikes, spot])

    // Start from a spread once the chain is known.
    useEffect(() => {
        if (atm && legs.length === 0) applyTemplate('Bull call spread')
    }, [atm]) // eslint-disable-line react-hooks/exhaustive-deps

    function applyTemplate(name) {
        if (!atm) return
        const idx = strikes.indexOf(atm)
        setLegs(
            TEMPLATES[name].map((t) => ({
                option_type: t.type,
                strike: strikes[Math.min(strikes.length - 1, Math.max(0, idx + t.off))] ?? atm + t.off * step,
                qty: t.qty,
                premium: '',
            })),
        )
    }

    // Recompute whenever anything changes, debounced so dragging stays smooth.
    useEffect(() => {
        if (!engine?.online || !spot || !legs.length || !daysLeft) return undefined
        clearTimeout(timer.current)
        timer.current = setTimeout(async () => {
            setBusy(true)
            try {
                const res = await researchApi.payoff({
                    legs: legs.map((l) => ({
                        option_type: l.option_type,
                        strike: Number(l.strike),
                        qty: Number(l.qty),
                        premium: l.premium === '' ? null : Number(l.premium),
                    })),
                    entry_spot: Number(spot),
                    expiry_days: Number(daysLeft),
                    risk_free_rate: rate / 100,
                    volatility: iv / 100,
                    multiplier: lot,
                    commission_rate: 0.0005,
                    spot_min: Number(spot) * 0.85,
                    spot_max: Number(spot) * 1.15,
                    spot_points: 121,
                    scenario_iv_values: [Math.max(1, iv - 5) / 100, iv / 100, (iv + 5) / 100],
                })
                setResult(res.data)
                setError(null)
            } catch (e) {
                const d = e?.response?.data?.detail
                setError(Array.isArray(d) ? d.map((x) => x.msg).join('; ') : d || e.message)
            } finally {
                setBusy(false)
            }
        }, 250)
        return () => clearTimeout(timer.current)
    }, [engine?.online, spot, legs, daysLeft, iv, rate, lot])

    const data = useMemo(() => {
        if (!result) return []
        const grid = result.scenario_grid
        return result.expiry_curve.spot.map((s, i) => {
            const row = { spot: Math.round(s), expiry: Math.round(result.expiry_curve.pnl[i]) }
            grid?.iv_values?.forEach((v, j) => {
                const gi = grid.spot.findIndex((gs) => Math.abs(gs - s) < 1e-6)
                if (gi >= 0) row[`iv${Math.round(v * 100)}`] = Math.round(grid.pnl[j][gi])
            })
            return row
        })
    }, [result])

    const setLeg = (i, patch) => setLegs((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)))
    const nudge = (i, dir) =>
        setLegs((ls) =>
            ls.map((l, j) => {
                if (j !== i) return l
                const k = strikes.indexOf(Number(l.strike))
                return { ...l, strike: k >= 0 ? strikes[Math.min(strikes.length - 1, Math.max(0, k + dir))] : Number(l.strike) + dir * step }
            }),
        )

    const s = result?.summary
    const g = result?.greeks

    return (
        <div className="space-y-4 animate-fade-in">
            <ResearchNav
                title="Options Lab"
                subtitle="Build an NSE option strategy on real strikes and lot sizes, and see its payoff and Greeks move as you change it."
                engine={engine}
                onEngineChange={reloadEngine}
                actions={
                    <button
                        onClick={() =>
                            navigate(`/research?q=${encodeURIComponent(`I'm considering this ${underlying} ${expiry} options position: ${legs.map((l) => `${l.qty > 0 ? 'buy' : 'sell'} ${Math.abs(l.qty)} lot ${l.strike} ${l.option_type}`).join(', ')}. Spot ${spot}. Assess the risk and what the market would have to do for it to pay off.`)}`)
                        }
                        disabled={!legs.length}
                        className="btn-ghost flex items-center gap-2 !py-1.5 text-xs"
                    >
                        <Bot className="h-3.5 w-3.5" /> Ask the agent
                    </button>
                }
            />
            <ErrorNote error={error} onRetry={() => setError(null)} />

            <div className="grid gap-4 xl:grid-cols-[22rem_1fr]">
                {/* Builder */}
                <div className="space-y-4">
                    <HudPanel title="Underlying">
                        <div className="space-y-3">
                            <Field label="Symbol">
                                <input
                                    list="opt-underlyings"
                                    value={underlying}
                                    onChange={(e) => {
                                        const v = e.target.value.toUpperCase()
                                        if (underlyings.includes(v)) setUnderlying(v)
                                    }}
                                    className="input-field w-full font-mono"
                                    placeholder="NIFTY, BANKNIFTY, RELIANCE…"
                                />
                                <datalist id="opt-underlyings">
                                    {underlyings.map((u) => <option key={u} value={u} />)}
                                </datalist>
                            </Field>
                            <div className="grid grid-cols-2 gap-2">
                                <Field label="Expiry">
                                    <select value={expiry} onChange={(e) => setExpiry(e.target.value)} className="input-field w-full font-mono text-xs">
                                        {expiries.slice(0, 12).map((x) => <option key={x}>{x}</option>)}
                                    </select>
                                </Field>
                                <Field label="Spot">
                                    <input type="number" value={spot ?? ''} onChange={(e) => setSpot(e.target.value)} className="input-field w-full font-mono" />
                                </Field>
                            </div>
                            <div className="flex justify-between font-mono text-[11px] text-dark-500">
                                <span>lot {lot}</span>
                                <span>step {step}</span>
                                <span>ATM {atm ?? '—'}</span>
                            </div>
                        </div>
                    </HudPanel>

                    <HudPanel title="Strategy">
                        <div className="flex flex-wrap gap-1">
                            {Object.keys(TEMPLATES).map((name) => (
                                <button key={name} onClick={() => applyTemplate(name)} disabled={!atm} className="press rounded border border-dark-700 px-2 py-1 text-[11px] text-dark-300 transition-colors hover:border-primary-400/40 hover:text-primary-200 disabled:opacity-40">
                                    {name}
                                </button>
                            ))}
                        </div>
                        <div className="mt-3 space-y-2">
                            {legs.map((l, i) => (
                                <div key={i} className="rounded border border-dark-800 bg-dark-950/50 p-2 animate-fade-in">
                                    <div className="flex items-center gap-2">
                                        <Segmented
                                            value={l.qty > 0 ? 'buy' : 'sell'}
                                            onChange={(v) => setLeg(i, { qty: Math.abs(l.qty) * (v === 'buy' ? 1 : -1) })}
                                            options={[{ id: 'buy', label: 'Buy' }, { id: 'sell', label: 'Sell' }]}
                                        />
                                        <Segmented
                                            value={l.option_type}
                                            onChange={(v) => setLeg(i, { option_type: v })}
                                            options={[{ id: 'call', label: 'CE' }, { id: 'put', label: 'PE' }]}
                                        />
                                        <button onClick={() => setLegs((ls) => ls.filter((_, j) => j !== i))} className="ml-auto text-dark-500 hover:text-danger-400">
                                            <Trash2 className="h-3.5 w-3.5" />
                                        </button>
                                    </div>
                                    <div className="mt-2 grid grid-cols-[1fr_4rem_5rem] items-center gap-2">
                                        <div className="flex items-center gap-1">
                                            <button onClick={() => nudge(i, -1)} className="rounded border border-dark-700 p-1 text-dark-400 hover:text-primary-300"><Minus className="h-3 w-3" /></button>
                                            <input value={l.strike} onChange={(e) => setLeg(i, { strike: e.target.value })} className="w-full rounded border border-dark-700 bg-dark-900 px-2 py-1 text-center font-mono text-xs text-dark-100" />
                                            <button onClick={() => nudge(i, 1)} className="rounded border border-dark-700 p-1 text-dark-400 hover:text-primary-300"><Plus className="h-3 w-3" /></button>
                                        </div>
                                        <input
                                            type="number"
                                            min={1}
                                            value={Math.abs(l.qty)}
                                            onChange={(e) => setLeg(i, { qty: Math.max(1, Number(e.target.value) || 1) * Math.sign(l.qty || 1) })}
                                            className="rounded border border-dark-700 bg-dark-900 px-2 py-1 text-center font-mono text-xs text-dark-100"
                                            title="Lots"
                                        />
                                        <input
                                            value={l.premium}
                                            onChange={(e) => setLeg(i, { premium: e.target.value })}
                                            placeholder="model"
                                            className="rounded border border-dark-700 bg-dark-900 px-2 py-1 text-center font-mono text-xs text-dark-100 placeholder:text-dark-600"
                                            title="Premium per unit; blank = Black-Scholes at your IV"
                                        />
                                    </div>
                                </div>
                            ))}
                            <button
                                onClick={() => setLegs((ls) => [...ls, { option_type: 'call', strike: atm || 0, qty: 1, premium: '' }])}
                                disabled={!atm}
                                className="flex w-full items-center justify-center gap-1 rounded border border-dashed border-dark-700 py-1.5 text-xs text-dark-400 hover:border-primary-400/40 hover:text-primary-300 disabled:opacity-40"
                            >
                                <Plus className="h-3 w-3" /> Add leg
                            </button>
                        </div>
                    </HudPanel>

                    <HudPanel title="Scenario">
                        <Slider label="Implied volatility" value={iv} min={5} max={60} step={0.5} unit="%" onChange={setIv} />
                        <Slider
                            label="Days to expiry"
                            value={daysLeft ?? 1}
                            min={0.1}
                            max={Math.max(1, Math.ceil(expiry ? daysTo(expiry) : 30))}
                            step={0.1}
                            unit="d"
                            onChange={setDaysLeft}
                        />
                        <p className="mt-2 text-[11px] text-dark-500">Blank premiums are Black-Scholes marks at this IV. Live premiums need a broker connection.</p>
                    </HudPanel>
                </div>

                {/* Output */}
                <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                        <Tile label={s?.entry_side === 'credit' ? 'Credit received' : 'Cost to enter'} value={s ? inr(Math.abs(s.entry_cost)) : '—'} />
                        <Tile label="Max profit" value={s ? (s.profit_unbounded ? 'unlimited' : inr(s.max_profit)) : '—'} tone="up" />
                        <Tile label="Max loss" value={s ? (s.loss_unbounded ? 'unlimited' : inr(s.max_loss)) : '—'} tone="down" />
                        <Tile label="Breakeven" value={s?.breakevens?.length ? s.breakevens.map((b) => Math.round(b)).join(' / ') : '—'} />
                    </div>

                    <HudPanel
                        title="Payoff"
                        subtitle={`${underlying} ${expiry} · P&L in ₹ for the whole position`}
                        right={busy && <Loader2 className="h-4 w-4 animate-spin text-primary-400" />}
                    >
                        {!engine?.online ? (
                            <Banner tone="danger">The research engine is offline; payoff maths runs there.</Banner>
                        ) : (
                            <div className="h-80">
                                <ResponsiveContainer width="100%" height="100%">
                                    <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 8 }}>
                                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                                        <XAxis dataKey="spot" type="number" domain={['dataMin', 'dataMax']} tick={{ fontSize: 10, fill: '#64748b' }} tickCount={8} />
                                        <YAxis tick={{ fontSize: 10, fill: '#64748b' }} width={70} tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
                                        <Tooltip
                                            contentStyle={{ background: '#020617', border: '1px solid #1e293b', fontSize: 12 }}
                                            formatter={(v, n) => [`₹${Number(v).toLocaleString('en-IN')}`, n === 'expiry' ? 'At expiry' : `Today @ IV ${n.slice(2)}%`]}
                                            labelFormatter={(l) => `Spot ${l}`}
                                        />
                                        <Legend wrapperStyle={{ fontSize: 11 }} formatter={(n) => (n === 'expiry' ? 'At expiry' : `Today, IV ${n.slice(2)}%`)} />
                                        <ReferenceLine y={0} stroke="#475569" />
                                        {spot && <ReferenceLine x={Math.round(spot)} stroke="#22d3ee" strokeDasharray="4 4" label={{ value: 'spot', fill: '#22d3ee', fontSize: 10, position: 'top' }} />}
                                        {(s?.breakevens || []).map((b) => (
                                            <ReferenceLine key={b} x={Math.round(b)} stroke="#fbbf24" strokeDasharray="2 4" />
                                        ))}
                                        {result?.scenario_grid?.iv_values?.map((v, j) => (
                                            <Line key={v} dataKey={`iv${Math.round(v * 100)}`} dot={false} strokeWidth={1.25} stroke={['#64748b', '#38bdf8', '#a78bfa'][j % 3]} isAnimationActive={false} />
                                        ))}
                                        <Line dataKey="expiry" dot={false} strokeWidth={2.25} stroke="#4ade80" isAnimationActive={false} />
                                    </ComposedChart>
                                </ResponsiveContainer>
                            </div>
                        )}
                    </HudPanel>

                    <HudPanel title="Greeks" subtitle="Position totals, today">
                        <div className="grid grid-cols-5 gap-3">
                            {['delta', 'gamma', 'theta', 'vega', 'rho'].map((k) => (
                                <Stat key={k} label={k} value={g ? Number(g[k]).toFixed(k === 'gamma' ? 4 : 1) : '—'} tone={g && g[k] < 0 ? 'down' : 'neutral'} />
                            ))}
                        </div>
                        {result?.limitations && (
                            <ul className="mt-3 list-disc space-y-0.5 pl-4 text-[11px] text-dark-500">
                                {result.limitations.map((l) => <li key={l}>{l}</li>)}
                            </ul>
                        )}
                    </HudPanel>
                </div>
            </div>
        </div>
    )
}

function Tile({ label, value, tone = 'neutral' }) {
    return (
        <div className="hud-panel px-4 py-3 transition-all">
            <Stat label={label} value={value} tone={tone} />
        </div>
    )
}

function Slider({ label, value, min, max, step, unit, onChange }) {
    return (
        <div className="mb-3">
            <div className="mb-1 flex justify-between">
                <span className="hud-label">{label}</span>
                <span className="font-mono text-xs text-primary-200">{Number(value).toFixed(step < 1 ? 1 : 0)}{unit}</span>
            </div>
            <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={value}
                onChange={(e) => onChange(Number(e.target.value))}
                className="w-full accent-cyan-400"
            />
        </div>
    )
}

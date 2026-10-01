import { useCallback, useEffect, useRef, useState } from 'react'
import {
    AlertTriangle, Brain, Building2, Layers, RefreshCw, Upload, Wallet,
} from 'lucide-react'
import { HudPanel, Stat, Meter, Delta, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import { wealthApi } from '../services/api'

/**
 * Consolidated wealth — every account and asset class in one view.
 *
 * The concentration and allocation panels are the reason this page exists:
 * risk that's invisible when positions sit in three separate broker apps
 * becomes obvious the moment they're merged.
 */

const CLASS_COLORS = {
    equity: '#22d3ee',
    etf: '#38bdf8',
    reit: '#a78bfa',
    invit: '#c084fc',
    bond: '#4ade80',
    gsec: '#34d399',
    gold: '#fbbf24',
    commodity: '#fb923c',
    cash: '#94a3b8',
    mutual_fund: '#60a5fa',
}

const inr = (n, digits = 0) =>
    `₹${Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: digits })}`

export default function Wealth() {
    const [data, setData] = useState(null)
    const [brokers, setBrokers] = useState(null)
    const [review, setReview] = useState(null)
    const [loading, setLoading] = useState(true)
    const [reviewing, setReviewing] = useState(false)
    const [error, setError] = useState(null)
    const [importing, setImporting] = useState(false)
    const [importResult, setImportResult] = useState(null)
    const fileRef = useRef(null)

    const load = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const [analytics, brokerStatus] = await Promise.all([
                wealthApi.analytics(),
                wealthApi.brokers(),
            ])
            setData(analytics.data)
            setBrokers(brokerStatus.data)
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        load()
    }, [load])

    const runReview = async () => {
        setReviewing(true)
        try {
            const { data: result } = await wealthApi.review()
            setReview(result)
        } catch (err) {
            setReview({ review: `Review failed: ${err?.response?.data?.detail || err.message}` })
        } finally {
            setReviewing(false)
        }
    }

    const handleImport = async (event) => {
        const file = event.target.files?.[0]
        if (!file) return
        setImporting(true)
        setImportResult(null)
        try {
            const { data: result } = await wealthApi.depositoryImport(file)
            setImportResult({ ok: true, ...result })
            await load()
        } catch (err) {
            setImportResult({ ok: false, error: err?.response?.data?.detail || err.message })
        } finally {
            setImporting(false)
            event.target.value = ''
        }
    }

    if (loading && !data) return <Loading rows={6} label="Consolidating accounts" />

    const empty = data?.empty
    const totals = data?.totals || {}
    const concentration = data?.concentration
    const allocation = data?.allocation
    const risk = data?.risk
    const income = data?.income

    return (
        <div className="space-y-5">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="font-mono text-lg uppercase tracking-[0.3em] text-primary-300 text-glow">
                        Consolidated Wealth
                    </h1>
                    <p className="mt-1 text-xs text-dark-400">
                        Every broker, depository and asset class in one position.
                    </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <input
                        ref={fileRef}
                        type="file"
                        accept=".csv,.txt"
                        onChange={handleImport}
                        className="hidden"
                    />
                    <button
                        onClick={() => fileRef.current?.click()}
                        className="btn-ghost"
                        disabled={importing}
                    >
                        <Upload className="h-3.5 w-3.5" />
                        {importing ? 'Importing…' : 'Import CAS'}
                    </button>
                    <button onClick={load} className="btn-ghost" disabled={loading}>
                        <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
                        Refresh
                    </button>
                    <button onClick={runReview} className="btn-primary" disabled={reviewing || empty}>
                        <Brain className="h-3.5 w-3.5" />
                        {reviewing ? 'Analysing…' : 'AI review'}
                    </button>
                </div>
            </header>

            <ErrorNote error={error} onRetry={load} />

            {importResult && (
                <div
                    className={`rounded border px-3 py-2 text-xs ${
                        importResult.ok
                            ? 'border-success-400/30 bg-success-500/10 text-success-300'
                            : 'border-danger-400/30 bg-danger-500/10 text-danger-300'
                    }`}
                >
                    {importResult.ok
                        ? `Imported ${importResult.imported} holdings: ${importResult.symbols?.join(', ')}`
                        : importResult.error}
                </div>
            )}

            {empty ? (
                <HudPanel>
                    <Empty
                        icon={Wallet}
                        title="No holdings yet"
                        hint={data.message}
                        action={
                            <button onClick={() => fileRef.current?.click()} className="btn-primary mt-2">
                                <Upload className="h-3.5 w-3.5" /> Import a CDSL/NSDL statement
                            </button>
                        }
                    />
                </HudPanel>
            ) : (
                <>
                    {/* ---- Headline ---- */}
                    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                        <HudPanel corners={false} glow>
                            <Stat label="Total value" value={inr(totals.current_value)} />
                        </HudPanel>
                        <HudPanel corners={false}>
                            <Stat label="Invested" value={inr(totals.invested)} tone="muted" />
                        </HudPanel>
                        <HudPanel corners={false}>
                            <Stat
                                label="Unrealised P&L"
                                value={inr(totals.pnl)}
                                tone={totals.pnl >= 0 ? 'up' : 'down'}
                                sub={`${totals.pnl_percent >= 0 ? '+' : ''}${totals.pnl_percent}%`}
                            />
                        </HudPanel>
                        <HudPanel corners={false}>
                            <Stat
                                label="Spread across"
                                value={`${totals.accounts} account${totals.accounts === 1 ? '' : 's'}`}
                                sub={`${totals.instruments} instruments`}
                            />
                        </HudPanel>
                    </div>

                    {review && (
                        <HudPanel title="AI portfolio review" glow>
                            <div className="space-y-2 text-sm leading-relaxed text-dark-200">
                                {(review.review || '').split('\n').map((line, i) =>
                                    line.trim() ? (
                                        <p key={i}>
                                            {line.replace(/\*\*/g, '').replace(/^[-*]\s*/, '• ')}
                                        </p>
                                    ) : null
                                )}
                            </div>
                        </HudPanel>
                    )}

                    <div className="grid gap-5 lg:grid-cols-2">
                        {/* ---- Concentration ---- */}
                        <HudPanel
                            title="Concentration"
                            subtitle="What you're actually betting on"
                            right={
                                <span
                                    className={
                                        concentration?.hhi_verdict === 'concentrated'
                                            ? 'badge-danger'
                                            : concentration?.hhi_verdict === 'moderate'
                                              ? 'badge-alert'
                                              : 'badge-success'
                                    }
                                >
                                    {concentration?.hhi_verdict}
                                </span>
                            }
                        >
                            <div className="space-y-4">
                                <div className="grid grid-cols-3 gap-3">
                                    <Stat label="HHI" value={concentration?.hhi} tone="primary" />
                                    <Stat
                                        label="Effective holdings"
                                        value={concentration?.effective_holdings}
                                        sub={`of ${concentration?.actual_holdings} held`}
                                    />
                                    <Stat label="Top 5 weight" value={`${concentration?.top_5_weight}%`} />
                                </div>

                                {concentration?.flags?.length > 0 ? (
                                    <div className="space-y-2">
                                        {concentration.flags.map((flag, i) => (
                                            <div
                                                key={i}
                                                className={`flex gap-2 rounded border px-3 py-2 text-xs leading-relaxed ${
                                                    flag.severity === 'high'
                                                        ? 'border-danger-400/30 bg-danger-500/8 text-danger-300'
                                                        : 'border-alert-400/30 bg-alert-500/8 text-alert-300'
                                                }`}
                                            >
                                                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                                <span>{flag.message}</span>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <p className="text-xs text-success-400">
                                        No concentration warnings — risk is spread sensibly.
                                    </p>
                                )}
                            </div>
                        </HudPanel>

                        {/* ---- Allocation ---- */}
                        <HudPanel
                            title="Asset allocation"
                            subtitle="Current mix vs your target"
                            right={
                                <span className="hud-label">
                                    {allocation?.diversification_score}/100
                                </span>
                            }
                        >
                            <div className="space-y-3">
                                {/* One stacked bar reads faster than a pie for "what am I made of". */}
                                <div className="flex h-2.5 overflow-hidden rounded-full bg-dark-800">
                                    {(data.by_asset_class || []).map((row) => (
                                        <div
                                            key={row.asset_class}
                                            style={{
                                                width: `${row.weight}%`,
                                                background: CLASS_COLORS[row.asset_class] || '#64748b',
                                            }}
                                            title={`${row.asset_class} ${row.weight}%`}
                                        />
                                    ))}
                                </div>

                                <div className="space-y-2">
                                    {(allocation?.allocation || [])
                                        .filter((r) => r.current_weight > 0 || r.target_weight > 0)
                                        .map((row) => (
                                            <div key={row.asset_class} className="flex items-center gap-3">
                                                <span
                                                    className="h-2 w-2 shrink-0 rounded-sm"
                                                    style={{ background: CLASS_COLORS[row.asset_class] || '#64748b' }}
                                                />
                                                <span className="w-16 shrink-0 font-mono text-[11px] uppercase text-dark-300">
                                                    {row.asset_class}
                                                </span>
                                                <div className="flex-1">
                                                    <Meter
                                                        value={row.current_weight}
                                                        max={Math.max(row.target_weight, row.current_weight, 10)}
                                                        tone={
                                                            row.action === 'add'
                                                                ? 'alert'
                                                                : row.action === 'trim'
                                                                  ? 'danger'
                                                                  : 'success'
                                                        }
                                                    />
                                                </div>
                                                <span className="w-24 shrink-0 text-right font-mono text-[11px] tabular-nums text-dark-400">
                                                    {row.current_weight}% / {row.target_weight}%
                                                </span>
                                                <span
                                                    className={`w-11 shrink-0 text-right font-mono text-[10px] uppercase ${
                                                        row.action === 'hold'
                                                            ? 'text-dark-600'
                                                            : row.action === 'add'
                                                              ? 'text-alert-400'
                                                              : 'text-danger-400'
                                                    }`}
                                                >
                                                    {row.action}
                                                </span>
                                            </div>
                                        ))}
                                </div>

                                {allocation?.missing_classes?.length > 0 && (
                                    <p className="pt-1 text-xs text-alert-300">
                                        No exposure at all to: {allocation.missing_classes.join(', ')}
                                    </p>
                                )}
                            </div>
                        </HudPanel>
                    </div>

                    <div className="grid gap-5 lg:grid-cols-3">
                        {/* ---- Risk ---- */}
                        <HudPanel title="Risk" subtitle={`vs ${risk?.benchmark || 'NIFTY 50'}`}>
                            <div className="space-y-3">
                                <div className="grid grid-cols-2 gap-3">
                                    <Stat
                                        label="Portfolio beta"
                                        value={risk?.portfolio_beta ?? '—'}
                                        sub={`${risk?.beta_coverage_percent}% covered`}
                                        tone="primary"
                                    />
                                    <Stat
                                        label="Volatility"
                                        value={
                                            risk?.weighted_annual_volatility_percent
                                                ? `${risk.weighted_annual_volatility_percent}%`
                                                : '—'
                                        }
                                        sub="annualised"
                                    />
                                </div>
                                <span className="badge-primary">{risk?.risk_level}</span>
                                <p className="text-xs leading-relaxed text-dark-400">
                                    {risk?.interpretation}
                                </p>
                            </div>
                        </HudPanel>

                        {/* ---- Income ---- */}
                        <HudPanel title="Income" subtitle="Cash the book throws off">
                            {income?.annual_income > 0 ? (
                                <div className="space-y-3">
                                    <div className="grid grid-cols-2 gap-3">
                                        <Stat label="Annual" value={inr(income.annual_income)} tone="up" />
                                        <Stat label="Monthly avg" value={inr(income.monthly_average)} />
                                    </div>
                                    <div className="flex items-center justify-between">
                                        <span className="hud-label">Portfolio yield</span>
                                        <span className="font-mono text-sm text-success-400">
                                            {income.portfolio_yield_percent}%
                                        </span>
                                    </div>
                                    <div className="space-y-1 pt-1">
                                        {income.contributors.slice(0, 4).map((c) => (
                                            <div
                                                key={c.symbol}
                                                className="flex items-center justify-between text-xs"
                                            >
                                                <span className="font-mono text-dark-300">{c.symbol}</span>
                                                <span className="font-mono text-dark-500">
                                                    {c.yield_percent}% · {inr(c.annual_income)}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            ) : (
                                <Empty
                                    title="No income"
                                    hint="Nothing held pays a distribution. REITs, InvITs and bonds would change that."
                                />
                            )}
                        </HudPanel>

                        {/* ---- Accounts ---- */}
                        <HudPanel title="Accounts" subtitle="Connected sources">
                            <div className="space-y-2">
                                {(brokers?.brokers || []).map((b) => (
                                    <div
                                        key={b.broker}
                                        className="flex items-center justify-between rounded border border-dark-700/60 px-3 py-2"
                                    >
                                        <div className="flex items-center gap-2">
                                            <Building2 className="h-3.5 w-3.5 text-dark-500" />
                                            <span className="text-xs text-dark-200">{b.display_name}</span>
                                        </div>
                                        <span
                                            className={
                                                b.connected
                                                    ? 'badge-success'
                                                    : b.configured
                                                      ? 'badge-alert'
                                                      : 'badge-muted'
                                            }
                                        >
                                            {b.connected ? 'live' : b.configured ? 'error' : 'not set'}
                                        </span>
                                    </div>
                                ))}
                                <a href="#/setup" className="btn-ghost mt-1 w-full">
                                    Connect a broker
                                </a>
                            </div>
                        </HudPanel>
                    </div>

                    {/* ---- Holdings ---- */}
                    <HudPanel title="Holdings" subtitle="Merged across every account" padded={false}>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b border-primary-400/10">
                                        {['Instrument', 'Class', 'Qty', 'Avg', 'LTP', 'Value', 'Weight', 'P&L'].map(
                                            (h, i) => (
                                                <th
                                                    key={h}
                                                    className={`px-4 py-2.5 hud-label ${i > 1 ? 'text-right' : 'text-left'}`}
                                                >
                                                    {h}
                                                </th>
                                            )
                                        )}
                                    </tr>
                                </thead>
                                <tbody>
                                    {(data.holdings || []).map((h) => (
                                        <tr
                                            key={h.symbol}
                                            className="border-b border-dark-800/60 transition-colors hover:bg-primary-500/5"
                                        >
                                            <td className="px-4 py-2.5">
                                                <div className="font-mono text-dark-100">{h.symbol}</div>
                                                <div className="truncate text-[11px] text-dark-500">
                                                    {h.name}
                                                    {h.held_across > 1 && (
                                                        <span className="ml-1.5 text-primary-400">
                                                            ×{h.held_across} accounts
                                                        </span>
                                                    )}
                                                </div>
                                            </td>
                                            <td className="px-4 py-2.5">
                                                <span
                                                    className="badge"
                                                    style={{
                                                        borderColor: `${CLASS_COLORS[h.asset_class]}55`,
                                                        color: CLASS_COLORS[h.asset_class] || '#94a3b8',
                                                    }}
                                                >
                                                    {h.asset_class}
                                                </span>
                                            </td>
                                            <td className="px-4 py-2.5 text-right font-mono tabular-nums text-dark-300">
                                                {h.quantity}
                                            </td>
                                            <td className="px-4 py-2.5 text-right font-mono tabular-nums text-dark-400">
                                                {inr(h.avg_price, 2)}
                                            </td>
                                            <td className="px-4 py-2.5 text-right font-mono tabular-nums text-dark-200">
                                                {inr(h.ltp, 2)}
                                            </td>
                                            <td className="px-4 py-2.5 text-right font-mono tabular-nums text-dark-100">
                                                {inr(h.current_value)}
                                            </td>
                                            <td className="px-4 py-2.5 text-right font-mono tabular-nums text-dark-400">
                                                {h.weight}%
                                            </td>
                                            <td className="px-4 py-2.5 text-right">
                                                <Delta value={h.pnl_percent} showArrow={false} />
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </HudPanel>

                    {/* ---- Sectors ---- */}
                    <HudPanel title="Sector exposure" subtitle="Where correlated risk hides">
                        <div className="space-y-2">
                            {(data.by_sector || []).map((s) => (
                                <div key={s.sector} className="flex items-center gap-3">
                                    <span className="w-40 shrink-0 truncate text-xs text-dark-300">
                                        {s.sector}
                                    </span>
                                    <div className="flex-1">
                                        <Meter
                                            value={s.weight}
                                            tone={s.weight > 40 ? 'danger' : s.weight > 25 ? 'alert' : 'primary'}
                                        />
                                    </div>
                                    <span className="w-14 shrink-0 text-right font-mono text-xs tabular-nums text-dark-400">
                                        {s.weight}%
                                    </span>
                                    <span className="w-10 shrink-0 text-right font-mono text-[10px] text-dark-600">
                                        {s.count}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </HudPanel>
                </>
            )}
        </div>
    )
}

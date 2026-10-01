import { useMemo } from 'react'
import {
    Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line,
    ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { AlertTriangle, Info } from 'lucide-react'

/**
 * Everything that renders a run.
 *
 * The ordering across this file is the argument the studio is making: the
 * equity curve is always shown against buy-and-hold, drawdown sits directly
 * underneath it rather than on a separate tab, and the concerns list is
 * placed above the metrics — not below, where a good-looking return would be
 * read first and the caveats treated as small print.
 */

const AXIS = { stroke: '#334155', fontSize: 10, fontFamily: 'JetBrains Mono' }

const TOOLTIP_STYLE = {
    contentStyle: {
        background: 'rgba(10,17,32,0.96)',
        border: '1px solid rgba(34,211,238,0.25)',
        borderRadius: 8,
        fontSize: 11,
        fontFamily: 'JetBrains Mono',
    },
    labelStyle: { color: '#67e8f9' },
}

const money = (value) =>
    `₹${Number(value || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

const pct = (value, digits = 1) =>
    value === null || value === undefined || Number.isNaN(value)
        ? '—'
        : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(digits)}%`

/* ------------------------------------------------------------------ */

export function Concerns({ items = [], title = 'What is wrong with this result' }) {
    if (!items.length) {
        return (
            <div className="flex items-center gap-2.5 rounded-lg border border-success-400/25 bg-success-500/5 px-3.5 py-3">
                <Info className="h-4 w-4 shrink-0 text-success-400" />
                <p className="text-xs text-success-300">
                    Nothing mechanically wrong with this result. That is not the same as it
                    being a good strategy — run the walk-forward.
                </p>
            </div>
        )
    }

    return (
        <div className="rounded-lg border border-alert-400/25 bg-alert-500/5 p-3.5">
            <div className="mb-2 flex items-center gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-alert-400" />
                <span className="hud-label !text-alert-300">{title}</span>
            </div>
            <ul className="space-y-1.5">
                {items.map((item, index) => (
                    <li key={index} className="flex gap-2 text-xs leading-relaxed text-alert-100/85">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-alert-400/70" />
                        <span>{item}</span>
                    </li>
                ))}
            </ul>
        </div>
    )
}

/* ------------------------------------------------------------------ */

const METRIC_GROUPS = [
    {
        label: 'Return',
        items: [
            { key: 'total_return_pct', label: 'Total return', format: pct, tone: true },
            { key: 'cagr_pct', label: 'CAGR', format: pct, tone: true },
            { key: 'benchmark_return_pct', label: 'Buy & hold', format: pct, tone: true },
            { key: 'alpha_pct', label: 'Difference', format: pct, tone: true },
        ],
    },
    {
        label: 'Risk',
        items: [
            { key: 'max_drawdown_pct', label: 'Max drawdown', format: (v) => `${Number(v || 0).toFixed(1)}%` },
            { key: 'max_drawdown_days', label: 'Underwater', format: (v) => `${v || 0} bars`,
              hint: 'Longest run from a peak until it was recovered' },
            { key: 'sharpe', label: 'Sharpe', format: (v) => Number(v || 0).toFixed(2) },
            { key: 'sortino', label: 'Sortino', format: (v) => Number(v || 0).toFixed(2),
              hint: 'Like Sharpe, but only counts downside deviation as risk' },
            { key: 'calmar', label: 'Calmar', format: (v) => Number(v || 0).toFixed(2),
              hint: 'CAGR divided by max drawdown' },
            { key: 'volatility_pct', label: 'Volatility', format: (v) => `${Number(v || 0).toFixed(1)}%` },
            { key: 'cvar_95_pct', label: 'CVaR 95%', format: (v) => `${Number(v || 0).toFixed(2)}%`,
              hint: 'Average daily loss on the worst 5% of days' },
        ],
    },
    {
        label: 'Activity',
        items: [
            { key: 'total_trades', label: 'Trades', format: (v) => v ?? 0 },
            { key: 'win_rate_pct', label: 'Win rate', format: (v) => `${Number(v || 0).toFixed(0)}%` },
            { key: 'profit_factor', label: 'Profit factor',
              format: (v) => (v === null || v === undefined ? '∞' : Number(v).toFixed(2)) },
            { key: 'expectancy_pct', label: 'Expectancy', format: (v) => `${Number(v || 0).toFixed(2)}%`,
              hint: 'Average return per trade, wins and losses together' },
            { key: 'avg_bars_held', label: 'Avg hold', format: (v) => `${Number(v || 0).toFixed(0)} bars` },
            { key: 'exposure_pct', label: 'In market', format: (v) => `${Number(v || 0).toFixed(0)}%` },
            { key: 'cost_drag_pct', label: 'Cost drag', format: (v) => `${Number(v || 0).toFixed(2)}%`,
              hint: 'What brokerage, taxes and slippage took, as % of starting capital' },
        ],
    },
]

export function MetricGrid({ metrics }) {
    if (!metrics) return null

    return (
        <div className="space-y-4">
            {METRIC_GROUPS.map((group) => (
                <div key={group.label}>
                    <div className="hud-label mb-2">{group.label}</div>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                        {group.items.map((item) => {
                            const raw = metrics[item.key]
                            const numeric = Number(raw)
                            const tone =
                                item.tone && !Number.isNaN(numeric)
                                    ? numeric >= 0
                                        ? 'text-success-400'
                                        : 'text-danger-400'
                                    : 'text-dark-50'
                            return (
                                <div
                                    key={item.key}
                                    className="material-raised rounded-lg px-3 py-2.5"
                                    title={item.hint || ''}
                                >
                                    <div className="truncate font-mono text-[9px] uppercase tracking-[0.14em] text-dark-500">
                                        {item.label}
                                    </div>
                                    <div className={`mt-0.5 font-mono text-base tabular-nums ${tone}`}>
                                        {item.format(raw)}
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                </div>
            ))}
        </div>
    )
}

/* ------------------------------------------------------------------ */

export function EquityCurve({ curve = [], height = 260, showBenchmark = true }) {
    // The memo runs before any early return: hooks must be called in the same
    // order on every render, and bailing out on an empty curve above it would
    // change that order the moment a result arrives.
    const data = useMemo(() => {
        if (!curve.length) return []
        const start = curve[0]?.equity || 1
        const benchStart = curve[0]?.benchmark || 1
        return curve.map((point) => ({
            ...point,
            // Both series indexed to 100 so the comparison is about shape and
            // separation, not about which started with more capital.
            strategy: (point.equity / start) * 100,
            bench: point.benchmark ? (point.benchmark / benchStart) * 100 : null,
        }))
    }, [curve])

    if (!data.length) return null

    return (
        <ResponsiveContainer width="100%" height={height}>
            <ComposedChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                <defs>
                    <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.26} />
                        <stop offset="100%" stopColor="#22d3ee" stopOpacity={0} />
                    </linearGradient>
                </defs>
                <CartesianGrid stroke="rgba(148,163,184,0.08)" vertical={false} />
                <XAxis dataKey="date" {...AXIS} tickLine={false} minTickGap={60} />
                <YAxis {...AXIS} tickLine={false} width={46}
                       tickFormatter={(v) => Math.round(v)} domain={['auto', 'auto']} />
                <Tooltip
                    {...TOOLTIP_STYLE}
                    formatter={(value, name) => [
                        `${Number(value).toFixed(1)}`,
                        name === 'strategy' ? 'Strategy' : 'Buy & hold',
                    ]}
                />
                <ReferenceLine y={100} stroke="rgba(148,163,184,0.3)" strokeDasharray="3 3" />
                {showBenchmark && (
                    <Line type="monotone" dataKey="bench" stroke="#64748b" strokeWidth={1.4}
                          dot={false} strokeDasharray="4 3" isAnimationActive={false} />
                )}
                <Area type="monotone" dataKey="strategy" stroke="#22d3ee" strokeWidth={1.8}
                      fill="url(#equityFill)" dot={false} isAnimationActive={false} />
            </ComposedChart>
        </ResponsiveContainer>
    )
}

export function DrawdownCurve({ curve = [], height = 110 }) {
    if (!curve.length || curve[0].drawdown === undefined) return null

    return (
        <ResponsiveContainer width="100%" height={height}>
            <AreaChart data={curve} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                <defs>
                    <linearGradient id="ddFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#f87171" stopOpacity={0} />
                        <stop offset="100%" stopColor="#f87171" stopOpacity={0.35} />
                    </linearGradient>
                </defs>
                <CartesianGrid stroke="rgba(148,163,184,0.06)" vertical={false} />
                <XAxis dataKey="date" {...AXIS} tickLine={false} minTickGap={80} hide />
                <YAxis {...AXIS} tickLine={false} width={46}
                       tickFormatter={(v) => `${Math.round(v)}%`} />
                <Tooltip {...TOOLTIP_STYLE}
                         formatter={(value) => [`${Number(value).toFixed(1)}%`, 'Drawdown']} />
                <Area type="monotone" dataKey="drawdown" stroke="#f87171" strokeWidth={1.2}
                      fill="url(#ddFill)" dot={false} isAnimationActive={false} />
            </AreaChart>
        </ResponsiveContainer>
    )
}

/* ------------------------------------------------------------------ */

export function MonthlyReturns({ months = [] }) {
    if (!months.length) return null

    const extreme = Math.max(...months.map((m) => Math.abs(m.return_pct)), 1)

    return (
        <div className="space-y-2">
            <div className="flex flex-wrap gap-1">
                {months.map((month) => {
                    const value = month.return_pct
                    const intensity = Math.min(1, Math.abs(value) / extreme)
                    const background =
                        value >= 0
                            ? `rgba(74, 222, 128, ${0.12 + intensity * 0.55})`
                            : `rgba(248, 113, 113, ${0.12 + intensity * 0.55})`
                    return (
                        <div
                            key={month.month}
                            title={`${month.month}: ${pct(value)}`}
                            className="flex h-9 w-[52px] flex-col items-center justify-center rounded"
                            style={{ background }}
                        >
                            <span className="font-mono text-[8px] text-dark-100/70">
                                {month.month.slice(2)}
                            </span>
                            <span className="font-mono text-[10px] tabular-nums text-dark-50">
                                {value >= 0 ? '+' : ''}
                                {value.toFixed(0)}
                            </span>
                        </div>
                    )
                })}
            </div>
        </div>
    )
}

/* ------------------------------------------------------------------ */

export function TradeTable({ trades = [], limit = 200 }) {
    if (!trades.length) {
        return (
            <p className="py-6 text-center text-xs text-dark-500">
                No trades. The entry condition never fired over this period.
            </p>
        )
    }

    return (
        <div className="max-h-[420px] overflow-auto">
            <table className="w-full text-left font-mono text-[11px]">
                <thead className="sticky top-0 z-10 bg-dark-900/95 backdrop-blur">
                    <tr className="text-dark-500">
                        {['Entry', 'Exit', 'Qty', 'In', 'Out', 'Bars', 'Net', 'Return', 'Why out']
                            .map((head) => (
                                <th key={head} className="px-2 py-2 font-normal uppercase tracking-wider">
                                    {head}
                                </th>
                            ))}
                    </tr>
                </thead>
                <tbody>
                    {trades.slice(0, limit).map((trade, index) => {
                        const win = trade.net_pnl >= 0
                        return (
                            <tr
                                key={index}
                                className="border-t border-dark-800/70 transition-colors hover:bg-dark-800/40"
                            >
                                <td className="px-2 py-1.5 text-dark-300">{trade.entry_date}</td>
                                <td className="px-2 py-1.5 text-dark-400">{trade.exit_date}</td>
                                <td className="px-2 py-1.5 tabular-nums text-dark-400">{trade.quantity}</td>
                                <td className="px-2 py-1.5 tabular-nums text-dark-300">{trade.entry_price}</td>
                                <td className="px-2 py-1.5 tabular-nums text-dark-300">{trade.exit_price}</td>
                                <td className="px-2 py-1.5 tabular-nums text-dark-500">{trade.bars_held}</td>
                                <td className={`px-2 py-1.5 tabular-nums ${win ? 'text-success-400' : 'text-danger-400'}`}>
                                    {money(trade.net_pnl)}
                                </td>
                                <td className={`px-2 py-1.5 tabular-nums ${win ? 'text-success-400' : 'text-danger-400'}`}>
                                    {pct(trade.return_pct, 2)}
                                </td>
                                <td className="max-w-[180px] truncate px-2 py-1.5 text-dark-500"
                                    title={trade.exit_reason}>
                                    {trade.exit_reason}
                                </td>
                            </tr>
                        )
                    })}
                </tbody>
            </table>
            {trades.length > limit && (
                <p className="px-2 py-2 text-[10px] text-dark-600">
                    Showing {limit} of {trades.length} trades.
                </p>
            )}
        </div>
    )
}

/* ------------------------------------------------------------------ */

export function WalkForwardPanel({ result }) {
    if (!result) return null

    const folds = (result.folds || []).filter((f) => !f.skipped)
    const efficiency = result.efficiency

    const chartData = folds.map((fold) => ({
        name: `F${fold.index}`,
        inSample: fold.is_return_pct,
        outSample: fold.oos_return_pct,
    }))

    return (
        <div className="space-y-5">
            {/* The headline: the drop, not the return */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div className="material-raised rounded-lg p-3.5">
                    <div className="hud-label">Out-of-sample return</div>
                    <div
                        className={`mt-1 font-mono text-2xl tabular-nums ${
                            (result.metrics?.total_return_pct ?? 0) >= 0
                                ? 'text-success-400'
                                : 'text-danger-400'
                        }`}
                    >
                        {pct(result.metrics?.total_return_pct)}
                    </div>
                    <div className="mt-0.5 text-[11px] text-dark-500">
                        buy &amp; hold {pct(result.metrics?.benchmark_return_pct)}
                    </div>
                </div>

                <div className="material-raised rounded-lg p-3.5">
                    <div className="hud-label">Efficiency</div>
                    <div
                        className={`mt-1 font-mono text-2xl tabular-nums ${
                            efficiency === null || efficiency === undefined
                                ? 'text-dark-400'
                                : efficiency >= 0.6
                                  ? 'text-success-400'
                                  : efficiency >= 0.3
                                    ? 'text-alert-400'
                                    : 'text-danger-400'
                        }`}
                    >
                        {efficiency === null || efficiency === undefined
                            ? '—'
                            : `${(efficiency * 100).toFixed(0)}%`}
                    </div>
                    <div className="mt-0.5 text-[11px] text-dark-500">
                        of the in-sample return survived
                    </div>
                </div>

                <div className="material-raised rounded-lg p-3.5">
                    <div className="hud-label">Consistency</div>
                    <div className="mt-1 font-mono text-2xl tabular-nums text-dark-50">
                        {Number(result.consistency_pct || 0).toFixed(0)}%
                    </div>
                    <div className="mt-0.5 text-[11px] text-dark-500">
                        of windows made money ({folds.length} tested)
                    </div>
                </div>
            </div>

            {result.verdict?.length > 0 && (
                <div className="rounded-lg border border-primary-400/20 bg-primary-500/5 p-3.5">
                    <ul className="space-y-1.5">
                        {result.verdict.map((line, index) => (
                            <li key={index} className="flex gap-2 text-xs leading-relaxed text-dark-200">
                                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary-400/70" />
                                <span>{line}</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {/* In-sample against out-of-sample, fold by fold. The gap is the finding. */}
            {chartData.length > 0 && (
                <div>
                    <div className="hud-label mb-2">In-sample vs out-of-sample, by fold</div>
                    <ResponsiveContainer width="100%" height={190}>
                        <BarChart data={chartData} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                            <CartesianGrid stroke="rgba(148,163,184,0.08)" vertical={false} />
                            <XAxis dataKey="name" {...AXIS} tickLine={false} />
                            <YAxis {...AXIS} tickLine={false} width={46}
                                   tickFormatter={(v) => `${Math.round(v)}%`} />
                            <Tooltip
                                {...TOOLTIP_STYLE}
                                formatter={(value, name) => [
                                    `${Number(value).toFixed(1)}%`,
                                    name === 'inSample' ? 'In-sample (fitted)' : 'Out-of-sample (real)',
                                ]}
                            />
                            <ReferenceLine y={0} stroke="rgba(148,163,184,0.35)" />
                            <Bar dataKey="inSample" fill="rgba(100,116,139,0.55)" radius={[2, 2, 0, 0]} />
                            <Bar dataKey="outSample" radius={[2, 2, 0, 0]}>
                                {chartData.map((entry, index) => (
                                    <Cell key={index}
                                          fill={entry.outSample >= 0 ? '#4ade80' : '#f87171'} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            )}

            {/* Parameter stability */}
            {result.parameter_stability?.length > 0 && (
                <div>
                    <div className="hud-label mb-2">
                        Parameters chosen on each window
                    </div>
                    <div className="space-y-1.5">
                        {result.parameter_stability.map((param) => (
                            <div
                                key={param.name}
                                className="flex items-center gap-3 rounded border border-dark-700/60 px-3 py-2"
                            >
                                <span className="w-28 shrink-0 truncate font-mono text-[11px] text-dark-300">
                                    {param.name}
                                </span>
                                <div className="flex flex-1 flex-wrap gap-1">
                                    {param.values.map((value, index) => (
                                        <span
                                            key={index}
                                            className="rounded bg-dark-800/70 px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-dark-300"
                                        >
                                            {value}
                                        </span>
                                    ))}
                                </div>
                                <span
                                    className={`shrink-0 font-mono text-[10px] uppercase tracking-wider ${
                                        param.stable ? 'text-success-400' : 'text-alert-400'
                                    }`}
                                >
                                    {param.stable ? 'stable' : 'unstable'}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Fold detail */}
            <div className="overflow-x-auto">
                <table className="w-full text-left font-mono text-[11px]">
                    <thead>
                        <tr className="text-dark-500">
                            {['Fold', 'In-sample window', 'IS return', 'Out-of-sample window',
                              'OOS return', 'Trades', 'OOS max DD'].map((head) => (
                                <th key={head} className="px-2 py-2 font-normal uppercase tracking-wider">
                                    {head}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {(result.folds || []).map((fold) => (
                            <tr key={fold.index} className="border-t border-dark-800/70">
                                <td className="px-2 py-1.5 text-dark-400">{fold.index}</td>
                                <td className="px-2 py-1.5 text-dark-500">{fold.is_period}</td>
                                <td className="px-2 py-1.5 tabular-nums text-dark-400">
                                    {fold.skipped ? '—' : pct(fold.is_return_pct)}
                                </td>
                                <td className="px-2 py-1.5 text-dark-500">{fold.oos_period}</td>
                                <td
                                    className={`px-2 py-1.5 tabular-nums ${
                                        fold.oos_return_pct >= 0 ? 'text-success-400' : 'text-danger-400'
                                    }`}
                                >
                                    {fold.skipped ? fold.skipped : pct(fold.oos_return_pct)}
                                </td>
                                <td className="px-2 py-1.5 tabular-nums text-dark-500">
                                    {fold.oos_trades}
                                </td>
                                <td className="px-2 py-1.5 tabular-nums text-dark-500">
                                    {fold.oos_max_drawdown_pct?.toFixed(1)}%
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

/* ------------------------------------------------------------------ */

export function SweepPanel({ result, onApply }) {
    if (!result) return null

    const cells = result.cells || []
    const tuned = result.tuned || []

    return (
        <div className="space-y-5">
            {result.verdict?.length > 0 && (
                <div className="rounded-lg border border-primary-400/20 bg-primary-500/5 p-3.5">
                    <ul className="space-y-1.5">
                        {result.verdict.map((line, index) => (
                            <li key={index} className="flex gap-2 text-xs leading-relaxed text-dark-200">
                                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary-400/70" />
                                <span>{line}</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <ParamCard
                    title="Highest scoring"
                    cell={result.best}
                    onApply={onApply}
                    tone="primary"
                />
                <ParamCard
                    title="Most stable"
                    cell={result.most_stable}
                    onApply={onApply}
                    tone="success"
                    hint="Neighbouring settings score nearly as well — prefer this."
                />
            </div>

            <div>
                <div className="hud-label mb-2">
                    Every combination · {result.combinations} total
                    {result.truncated ? ` (sampled ${cells.length ? 'evenly' : ''})` : ''}
                </div>
                <div className="max-h-[380px] overflow-auto">
                    <table className="w-full text-left font-mono text-[11px]">
                        <thead className="sticky top-0 z-10 bg-dark-900/95 backdrop-blur">
                            <tr className="text-dark-500">
                                {tuned.map((name) => (
                                    <th key={name} className="px-2 py-2 font-normal uppercase tracking-wider">
                                        {name}
                                    </th>
                                ))}
                                {['Score', 'Stability', 'Return', 'Sharpe', 'Max DD', 'Trades', '']
                                    .map((head) => (
                                        <th key={head} className="px-2 py-2 font-normal uppercase tracking-wider">
                                            {head}
                                        </th>
                                    ))}
                            </tr>
                        </thead>
                        <tbody>
                            {cells.map((cell, index) => (
                                <tr key={index} className="border-t border-dark-800/70 hover:bg-dark-800/40">
                                    {tuned.map((name) => (
                                        <td key={name} className="px-2 py-1.5 tabular-nums text-dark-200">
                                            {cell.params[name]}
                                        </td>
                                    ))}
                                    <td className="px-2 py-1.5 tabular-nums text-primary-300">
                                        {cell.score.toFixed(3)}
                                    </td>
                                    <td className="px-2 py-1.5">
                                        <StabilityBar value={cell.stability} />
                                    </td>
                                    <td
                                        className={`px-2 py-1.5 tabular-nums ${
                                            cell.metrics.total_return_pct >= 0
                                                ? 'text-success-400'
                                                : 'text-danger-400'
                                        }`}
                                    >
                                        {pct(cell.metrics.total_return_pct)}
                                    </td>
                                    <td className="px-2 py-1.5 tabular-nums text-dark-300">
                                        {cell.metrics.sharpe}
                                    </td>
                                    <td className="px-2 py-1.5 tabular-nums text-dark-400">
                                        {cell.metrics.max_drawdown_pct}%
                                    </td>
                                    <td className="px-2 py-1.5 tabular-nums text-dark-500">
                                        {cell.metrics.total_trades}
                                    </td>
                                    <td className="px-2 py-1.5">
                                        <button
                                            onClick={() => onApply?.(cell.params)}
                                            className="rounded border border-dark-600/70 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-dark-400 transition-colors hover:border-primary-400/50 hover:text-primary-300"
                                        >
                                            use
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    )
}

function StabilityBar({ value = 0 }) {
    const percent = Math.round(value * 100)
    const tone = value >= 0.7 ? 'bg-success-400' : value >= 0.4 ? 'bg-alert-400' : 'bg-danger-400'
    return (
        <div className="flex items-center gap-1.5" title={`${percent}% — neighbour scores relative to this cell`}>
            <div className="h-1 w-12 overflow-hidden rounded-full bg-dark-800">
                <div className={`h-full ${tone}`} style={{ width: `${percent}%` }} />
            </div>
            <span className="tabular-nums text-dark-500">{percent}</span>
        </div>
    )
}

function ParamCard({ title, cell, onApply, tone = 'primary', hint }) {
    if (!cell) {
        return (
            <div className="material-raised rounded-lg p-3.5">
                <div className="hud-label">{title}</div>
                <p className="mt-2 text-xs text-dark-500">
                    No setting qualified — nothing sat on a plateau.
                </p>
            </div>
        )
    }

    const border = tone === 'success' ? 'border-success-400/30' : 'border-primary-400/30'

    return (
        <div className={`material-raised rounded-lg border ${border} p-3.5`}>
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="hud-label">{title}</div>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {Object.entries(cell.params).map(([key, value]) => (
                            <span key={key} className="badge-muted">
                                {key} {String(value)}
                            </span>
                        ))}
                    </div>
                </div>
                <button onClick={() => onApply?.(cell.params)} className="btn-ghost shrink-0 !px-2 !py-1 !text-[10px]">
                    Apply
                </button>
            </div>
            <div className="mt-3 flex items-center gap-4 font-mono text-[11px]">
                <span className={cell.metrics.total_return_pct >= 0 ? 'text-success-400' : 'text-danger-400'}>
                    {pct(cell.metrics.total_return_pct)}
                </span>
                <span className="text-dark-400">Sharpe {cell.metrics.sharpe}</span>
                <span className="text-dark-400">DD {cell.metrics.max_drawdown_pct}%</span>
                <span className="text-dark-500">{cell.metrics.total_trades} trades</span>
            </div>
            {hint && <p className="mt-2 text-[11px] leading-relaxed text-dark-500">{hint}</p>}
        </div>
    )
}

/* ------------------------------------------------------------------ */

export function ComparisonTable({ result, onPick }) {
    if (!result?.results?.length) return null

    return (
        <div className="space-y-3">
            <p className="text-xs leading-relaxed text-dark-400">{result.verdict}</p>
            <div className="max-h-[520px] overflow-auto">
                <table className="w-full text-left font-mono text-[11px]">
                    <thead className="sticky top-0 z-10 bg-dark-900/95 backdrop-blur">
                        <tr className="text-dark-500">
                            {['Strategy', 'Type', 'Return', 'CAGR', 'Sharpe', 'Max DD', 'Trades',
                              'Win', 'In mkt', 'vs hold', ''].map((head) => (
                                <th key={head} className="px-2 py-2 font-normal uppercase tracking-wider">
                                    {head}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {result.results.map((row) => (
                            <tr
                                key={row.key}
                                className={`border-t border-dark-800/70 hover:bg-dark-800/40 ${
                                    row.key === 'buy_and_hold' ? 'bg-primary-500/[0.06]' : ''
                                }`}
                            >
                                <td className="max-w-[190px] truncate px-2 py-1.5 text-dark-100">
                                    {row.name}
                                </td>
                                <td className="px-2 py-1.5 text-dark-600">{row.category || '—'}</td>
                                {row.error ? (
                                    <td colSpan={8} className="px-2 py-1.5 text-danger-400/80">
                                        {row.error}
                                    </td>
                                ) : (
                                    <>
                                        <td className={`px-2 py-1.5 tabular-nums ${
                                            row.total_return_pct >= 0 ? 'text-success-400' : 'text-danger-400'
                                        }`}>
                                            {pct(row.total_return_pct)}
                                        </td>
                                        <td className="px-2 py-1.5 tabular-nums text-dark-300">
                                            {pct(row.cagr_pct)}
                                        </td>
                                        <td className="px-2 py-1.5 tabular-nums text-dark-300">{row.sharpe}</td>
                                        <td className="px-2 py-1.5 tabular-nums text-dark-400">
                                            {row.max_drawdown_pct}%
                                        </td>
                                        <td className="px-2 py-1.5 tabular-nums text-dark-500">
                                            {row.total_trades}
                                        </td>
                                        <td className="px-2 py-1.5 tabular-nums text-dark-500">
                                            {row.win_rate_pct}%
                                        </td>
                                        <td className="px-2 py-1.5 tabular-nums text-dark-500">
                                            {row.exposure_pct}%
                                        </td>
                                        <td className="px-2 py-1.5">
                                            <span className={row.beats_benchmark ? 'badge-success' : 'badge-muted'}>
                                                {row.beats_benchmark ? 'ahead' : 'behind'}
                                            </span>
                                        </td>
                                    </>
                                )}
                                <td className="px-2 py-1.5">
                                    <button
                                        onClick={() => onPick?.(row.key)}
                                        className="rounded border border-dark-600/70 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-dark-400 transition-colors hover:border-primary-400/50 hover:text-primary-300"
                                    >
                                        open
                                    </button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

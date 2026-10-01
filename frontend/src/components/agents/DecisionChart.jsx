import {
    Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend,
    Line, LineChart, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart,
    ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

/**
 * Charts that explain a decision.
 *
 * Every chart here is rendered from a spec produced by the backend step that
 * computed the numbers. The UI deliberately does not decide how to plot
 * anything — a chart assembled here from a bag of results is a chart that can
 * quietly misrepresent what the analysis actually did.
 */

// Axis furniture is deliberately quiet. On a dark HUD the data should be the
// only thing emitting light; gridlines that compete with the series make a
// chart harder to read, not easier.
const AXIS = {
    stroke: 'rgba(148,163,184,0.35)',
    fontSize: 10,
    tickLine: false,
    axisLine: false,
    letterSpacing: '0.02em',
}
const GRID = 'rgba(148,163,184,0.07)'

// Desaturated a step from the raw palette: fully saturated bars on a near-black
// ground vibrate and read as alarming regardless of what they mean.
const TONE = {
    positive: '#4ade80',
    negative: '#f87171',
    base: '#64748b',
    total: '#22d3ee',
    price: '#fbbf24',
    method: '#7dd3fc',
    result: '#22d3ee',
    stop: '#f87171',
    entry: '#cbd5e1',
    target: '#4ade80',
}

function Frame({ title, caption, children, height = 220 }) {
    return (
        <div className="material-panel materialize rounded-xl p-3.5">
            <p className="type-label vibrant-secondary mb-2.5">{title}</p>
            <div style={{ height }}>
                <ResponsiveContainer width="100%" height="100%">
                    {children}
                </ResponsiveContainer>
            </div>
            {caption && (
                <p className="type-caption vibrant-tertiary mt-2.5">{caption}</p>
            )}
        </div>
    )
}

const tip = {
    contentStyle: {
        background: 'rgba(10, 17, 32, 0.92)',
        backdropFilter: 'blur(12px)',
        border: '1px solid rgba(148,163,184,0.18)',
        borderRadius: 10,
        fontSize: 11,
        boxShadow: '0 8px 28px -10px rgba(0,0,0,0.8)',
    },
    labelStyle: { color: 'rgba(203,213,225,0.75)', fontSize: 10, letterSpacing: '0.04em' },
    cursor: { fill: 'rgba(148,163,184,0.06)' },
}

/**
 * The waterfall is the most important chart in the product: it turns a
 * conviction number from an assertion into arithmetic you can dispute one term
 * at a time. Each bar floats from the running total, so its height is literally
 * that factor's contribution.
 */
function Waterfall({ chart }) {
    const rows = chart.data.map((row, i) => {
        const previous = i === 0 ? 0 : chart.data[i - 1].cumulative
        if (row.type === 'base' || row.type === 'total') {
            return { ...row, floor: 0, bar: row.cumulative }
        }
        return {
            ...row,
            floor: Math.min(previous, row.cumulative),
            bar: Math.abs(row.value),
        }
    })

    return (
        <Frame title={chart.title} caption={chart.caption} height={300}>
            <BarChart data={rows} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="name" {...AXIS} angle={-32} textAnchor="end" interval={0} height={104} />
                <YAxis {...AXIS} domain={[0, 100]} />
                <Tooltip
                    {...tip}
                    formatter={(_v, _n, item) =>
                        [`${item.payload.value > 0 ? '+' : ''}${item.payload.value}`, 'contribution']
                    }
                />
                {/* Transparent floor bar is what makes each segment float. */}
                <Bar dataKey="floor" stackId="w" fill="transparent" />
                <Bar dataKey="bar" stackId="w" radius={[3, 3, 0, 0]}>
                    {rows.map((row, i) => (
                        <Cell key={i} fill={TONE[row.type] || TONE.base} />
                    ))}
                </Bar>
                <ReferenceLine y={50} stroke="#374151" strokeDasharray="3 3" />
            </BarChart>
        </Frame>
    )
}

/**
 * Price versus what each valuation method says it is worth.
 *
 * Note on layout: XAxis `height` already reserves room for the rotated labels.
 * Adding margin.bottom on top of it double-counts the space and crops them.
 */
function Bridge({ chart }) {
    return (
        <Frame title={chart.title} caption={chart.caption} height={290}>
            <BarChart data={chart.data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="name" {...AXIS} angle={-28} textAnchor="end" interval={0} height={96} />
                <YAxis {...AXIS} />
                <Tooltip {...tip} formatter={(v) => [`₹${Number(v).toLocaleString('en-IN')}`, 'value']} />
                <Bar dataKey="value" radius={[3, 3, 0, 0]}>
                    {chart.data.map((row, i) => (
                        <Cell key={i} fill={TONE[row.type] || TONE.method} />
                    ))}
                </Bar>
                {/* The market price as a line makes "cheap or dear" instantly readable. */}
                <ReferenceLine
                    y={chart.data.find((r) => r.type === 'price')?.value}
                    stroke={TONE.price}
                    strokeDasharray="4 3"
                />
            </BarChart>
        </Frame>
    )
}

function Series({ chart }) {
    const { config } = chart
    // The verdict's risk/reward geometry reuses "series" but is categorical.
    if (config.orientation === 'horizontal') {
        return (
            <Frame title={chart.title} caption={chart.caption} height={200}>
                <BarChart data={chart.data} layout="vertical" margin={{ left: 24, right: 16 }}>
                    <CartesianGrid stroke={GRID} horizontal={false} />
                    <XAxis type="number" {...AXIS} domain={['dataMin - 40', 'dataMax + 40']} />
                    <YAxis type="category" dataKey="name" {...AXIS} width={64} />
                    <Tooltip {...tip} formatter={(v) => [`₹${Number(v).toLocaleString('en-IN')}`, '']} />
                    <Bar dataKey="value" radius={[0, 3, 3, 0]}>
                        {chart.data.map((row, i) => (
                            <Cell key={i} fill={TONE[row.type] || TONE.method} />
                        ))}
                    </Bar>
                </BarChart>
            </Frame>
        )
    }

    return (
        <Frame title={chart.title} caption={chart.caption} height={230}>
            <ComposedChart data={chart.data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey={config.x} {...AXIS} minTickGap={44} />
                <YAxis {...AXIS} domain={['dataMin - 10', 'dataMax + 10']} />
                <Tooltip {...tip} />
                {config.band && (
                    <>
                        <Area dataKey={config.band.upper} stroke="none" fill="#22d3ee" fillOpacity={0.07} />
                        <Area dataKey={config.band.lower} stroke="none" fill="#0b0f19" fillOpacity={1} />
                    </>
                )}
                {(config.lines || []).map((line) => (
                    <Line
                        key={line.key}
                        type="monotone"
                        dataKey={line.key}
                        name={line.label}
                        stroke={line.emphasis ? '#22d3ee' : '#6b7280'}
                        strokeWidth={line.emphasis ? 2 : 1}
                        dot={false}
                    />
                ))}
            </ComposedChart>
        </Frame>
    )
}

function Bars({ chart }) {
    const { config } = chart
    return (
        <Frame title={chart.title} caption={chart.caption} height={230}>
            <BarChart data={chart.data} margin={{ top: 8, right: 8, left: -12, bottom: 34 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey={config.x} {...AXIS} angle={-30} textAnchor="end" interval={0} height={50} />
                <YAxis {...AXIS} />
                <Tooltip {...tip} />
                <Legend wrapperStyle={{ fontSize: 10, letterSpacing: '0.03em' }} />
                {(config.bars || []).map((bar, i) => (
                    <Bar
                        key={bar.key}
                        dataKey={bar.key}
                        name={bar.label}
                        fill={i === 0 ? '#22d3ee' : '#60a5fa'}
                        radius={[3, 3, 0, 0]}
                    />
                ))}
                {config.threshold && (
                    <ReferenceLine y={config.threshold} stroke="#f87171" strokeDasharray="4 3" />
                )}
            </BarChart>
        </Frame>
    )
}

function Equity({ chart }) {
    return (
        <Frame title={chart.title} caption={chart.caption} height={230}>
            <ComposedChart data={chart.data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="n" {...AXIS} />
                <YAxis {...AXIS} />
                <Tooltip {...tip} formatter={(v, n) => [
                    n === 'drawdown' ? `${v}%` : `₹${Number(v).toLocaleString('en-IN')}`, n,
                ]} />
                <ReferenceLine y={chart.config.start} stroke="#374151" strokeDasharray="3 3" />
                <Area dataKey="equity" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.12} />
            </ComposedChart>
        </Frame>
    )
}

function Gauge({ chart }) {
    const value = chart.data[0]?.value ?? 0
    const { min = 0, max = 2, bands = [] } = chart.config
    const pct = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100))
    const tones = { good: 'bg-emerald-500/70', neutral: 'bg-cyan-500/70', warn: 'bg-amber-500/70' }

    return (
        <div className="material-panel materialize rounded-xl p-3.5">
            <p className="type-label vibrant-secondary mb-3">{chart.title}</p>
            <div className="flex items-baseline gap-2">
                <span className="type-numeric text-3xl font-medium text-primary-300">{value}</span>
                <span className="type-caption vibrant-secondary">
                    {bands.find((b) => value <= b.to)?.label || ''}
                </span>
            </div>
            <div className="mt-3 flex h-2 overflow-hidden rounded-full">
                {bands.map((band, i) => {
                    const from = i === 0 ? min : bands[i - 1].to
                    return (
                        <div
                            key={band.label}
                            className={tones[band.tone] || 'bg-dark-600'}
                            style={{ width: `${((band.to - from) / (max - min)) * 100}%` }}
                        />
                    )
                })}
            </div>
            <div className="relative h-3">
                <div
                    className="absolute top-0 h-3 w-0.5 bg-white"
                    style={{ left: `${pct}%` }}
                />
            </div>
            {chart.caption && (
                <p className="type-caption vibrant-tertiary mt-1.5">{chart.caption}</p>
            )}
        </div>
    )
}

function FactorRadar({ chart }) {
    return (
        <Frame title={chart.title} caption={chart.caption} height={250}>
            <RadarChart data={chart.data} outerRadius="72%">
                <PolarGrid stroke={GRID} />
                <PolarAngleAxis dataKey="factor" tick={{ fill: 'rgba(203,213,225,0.7)', fontSize: 9, letterSpacing: '0.03em' }} />
                <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
                <Radar dataKey="score" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.28} />
                <Tooltip {...tip} />
            </RadarChart>
        </Frame>
    )
}

const RENDERERS = {
    waterfall: Waterfall,
    bridge: Bridge,
    series: Series,
    bar: Bars,
    equity: Equity,
    gauge: Gauge,
    radar: FactorRadar,
}

export default function DecisionChart({ chart }) {
    const Renderer = RENDERERS[chart.kind]
    if (!Renderer || !chart.data?.length) return null
    return <Renderer chart={chart} />
}

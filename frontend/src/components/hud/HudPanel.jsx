import AnimatedValue from './AnimatedValue'
/**
 * Shared HUD primitives.
 *
 * Every readout in the app is built from these so the interface reads as one
 * instrument rather than a collection of cards.
 */

export function HudPanel({
    title,
    subtitle,
    right,
    children,
    className = '',
    corners = true,
    glow = false,
    padded = true,
}) {
    return (
        <section
            className={`${glow ? 'hud-panel-glow' : 'hud-panel'} ${corners ? 'hud-corners' : ''} ${className}`}
        >
            {(title || right) && (
                <header className="flex items-start justify-between gap-4 border-b border-primary-400/10 px-4 py-3">
                    <div className="min-w-0">
                        {title && <h2 className="hud-title truncate">{title}</h2>}
                        {subtitle && (
                            <p className="mt-0.5 truncate text-xs text-dark-400">{subtitle}</p>
                        )}
                    </div>
                    {right && <div className="shrink-0">{right}</div>}
                </header>
            )}
            <div className={padded ? 'p-4' : ''}>{children}</div>
        </section>
    )
}

/** A labelled figure. `tone` colours the value for P&L or status. */
export function Stat({ label, value, sub, tone = 'neutral', mono = true }) {
    const toneClass = {
        neutral: 'text-dark-50',
        up: 'text-success-400',
        down: 'text-danger-400',
        primary: 'text-primary-300',
        alert: 'text-alert-300',
        muted: 'text-dark-400',
    }[tone]

    return (
        <div className="min-w-0">
            <div className="hud-label truncate">{label}</div>
            <div
                className={`mt-1 truncate text-xl ${toneClass} ${mono ? 'font-mono tabular-nums' : 'font-semibold'}`}
            >
                {typeof value === 'string' || typeof value === 'number' ? <AnimatedValue value={value} /> : value}
            </div>
            {sub && <div className="mt-0.5 truncate text-xs text-dark-500">{sub}</div>}
        </div>
    )
}

/** Horizontal bar for a 0-100 reading. */
export function Meter({ value, max = 100, tone = 'primary', className = '' }) {
    const pct = Math.max(0, Math.min(100, (value / max) * 100))
    const color = {
        primary: 'bg-primary-400',
        success: 'bg-success-400',
        danger: 'bg-danger-400',
        alert: 'bg-alert-400',
    }[tone]

    return (
        <div className={`meter ${className}`}>
            <div className={`meter-fill ${color}`} style={{ width: `${pct}%` }} />
        </div>
    )
}

/** Signed percentage with an arrow, coloured by direction. */
export function Delta({ value, className = '', showArrow = true }) {
    if (value === null || value === undefined || Number.isNaN(value)) {
        return <span className={`font-mono text-dark-500 ${className}`}>—</span>
    }
    const up = value >= 0
    return (
        <span
            className={`font-mono tabular-nums ${up ? 'text-success-400' : 'text-danger-400'} ${className}`}
        >
            {showArrow && (up ? '▲ ' : '▼ ')}
            {up ? '+' : ''}
            {Number(value).toFixed(2)}%
        </span>
    )
}

/** Small status dot with an optional label. */
export function StatusDot({ status = 'ok', label, pulse = true }) {
    const color = {
        ok: 'bg-success-400',
        warn: 'bg-alert-400',
        error: 'bg-danger-400',
        idle: 'bg-dark-500',
        active: 'bg-primary-400',
    }[status]

    return (
        <span className="inline-flex items-center gap-2">
            <span className="relative flex h-2 w-2">
                {pulse && status !== 'idle' && (
                    <span className={`absolute inline-flex h-full w-full rounded-full ${color} opacity-60 animate-ping`} />
                )}
                <span className={`relative inline-flex h-2 w-2 rounded-full ${color}`} />
            </span>
            {label && <span className="hud-label">{label}</span>}
        </span>
    )
}

/** Placeholder for an empty region — never leave a panel blank. */
export function Empty({ icon: Icon, title, hint, action }) {
    return (
        <div className="flex flex-col items-center justify-center gap-3 px-6 py-10 text-center">
            {Icon && <Icon className="h-7 w-7 text-dark-600" />}
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-dark-400">{title}</p>
            {hint && <p className="max-w-sm text-sm leading-relaxed text-dark-500">{hint}</p>}
            {action}
        </div>
    )
}

/** Skeleton rows while data loads. */
export function Loading({ rows = 3, label = 'Acquiring data' }) {
    return (
        <div className="space-y-3 py-2">
            <div className="hud-label animate-pulse">{label}…</div>
            {Array.from({ length: rows }).map((_, i) => (
                <div
                    key={i}
                    className="shimmer h-9 rounded"
                    style={{ animationDelay: `${i * 90}ms` }}
                />
            ))}
        </div>
    )
}

export function ErrorNote({ error, onRetry }) {
    if (!error) return null
    return (
        <div className="flex items-center justify-between gap-3 rounded border border-danger-400/30 bg-danger-500/10 px-3 py-2">
            <p className="text-xs text-danger-300">{String(error)}</p>
            {onRetry && (
                <button onClick={onRetry} className="btn-ghost !px-2 !py-1 !text-[10px]">
                    Retry
                </button>
            )}
        </div>
    )
}

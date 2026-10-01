import { useMemo } from 'react'

/**
 * The core — Tradeo's presence on screen.
 *
 * Drawn as inline SVG rather than nested divs so the rings stay crisp at any
 * size and the whole thing scales with one prop. It reports state through
 * colour and motion: idle breathes slowly, listening reacts to your voice,
 * thinking spins up, speaking pulses outward.
 */

const STATES = {
    idle: { ring: '#22d3ee', glow: 0.35, spin: 'animate-spin-slow', label: 'STANDBY' },
    listening: { ring: '#22d3ee', glow: 0.85, spin: 'animate-spin-slow', label: 'LISTENING' },
    thinking: { ring: '#a5f3fc', glow: 0.7, spin: 'animate-spin', label: 'PROCESSING' },
    speaking: { ring: '#4ade80', glow: 0.8, spin: 'animate-spin-slow', label: 'RESPONDING' },
    alert: { ring: '#fbbf24', glow: 0.9, spin: 'animate-spin-slow', label: 'ALERT' },
    offline: { ring: '#475569', glow: 0.12, spin: '', label: 'OFFLINE' },
}

export default function CoreOrb({
    state = 'idle',
    amplitude = 0,
    size = 200,
    showLabel = true,
    onClick,
}) {
    const config = STATES[state] || STATES.idle

    // Voice amplitude drives the inner core, damped so it pulses rather than
    // strobes. Only listening state reacts — otherwise it looks possessed.
    const reactive = state === 'listening' ? amplitude : 0
    const coreScale = 1 + reactive * 0.28
    const glowOpacity = Math.min(1, config.glow + reactive * 0.4)

    // Tick marks around the outer ring. Memoised so they don't regenerate on
    // every amplitude frame.
    const ticks = useMemo(
        () =>
            Array.from({ length: 60 }, (_, i) => ({
                angle: i * 6,
                long: i % 5 === 0,
            })),
        []
    )

    return (
        <div
            className={`relative inline-flex flex-col items-center ${onClick ? 'cursor-pointer' : ''}`}
            style={{ width: size, height: size }}
            onClick={onClick}
            role={onClick ? 'button' : undefined}
            aria-label={onClick ? `Tradeo core — ${config.label}` : undefined}
        >
            {/* Ambient bloom behind everything */}
            <div
                className="pointer-events-none absolute inset-0 rounded-full blur-2xl transition-opacity duration-700"
                style={{
                    background: `radial-gradient(circle, ${config.ring}55 0%, transparent 65%)`,
                    opacity: glowOpacity,
                }}
            />

            <svg
                viewBox="0 0 200 200"
                className="relative"
                style={{ width: size, height: size }}
            >
                <defs>
                    <radialGradient id="coreFill">
                        <stop offset="0%" stopColor={config.ring} stopOpacity="0.95" />
                        <stop offset="55%" stopColor={config.ring} stopOpacity="0.35" />
                        <stop offset="100%" stopColor={config.ring} stopOpacity="0" />
                    </radialGradient>
                    <linearGradient id="arcFade" x1="0" y1="0" x2="1" y2="1">
                        <stop offset="0%" stopColor={config.ring} stopOpacity="0.9" />
                        <stop offset="100%" stopColor={config.ring} stopOpacity="0.1" />
                    </linearGradient>
                </defs>

                {/* Tick ring */}
                <g opacity="0.45">
                    {ticks.map(({ angle, long }) => (
                        <line
                            key={angle}
                            x1="100"
                            y1={long ? 8 : 11}
                            x2="100"
                            y2={long ? 16 : 14}
                            stroke={config.ring}
                            strokeWidth={long ? 1.2 : 0.6}
                            opacity={long ? 0.8 : 0.35}
                            transform={`rotate(${angle} 100 100)`}
                        />
                    ))}
                </g>

                {/* Counter-rotating arcs — the sense of an instrument at work */}
                <g className={config.spin} style={{ transformOrigin: '100px 100px' }}>
                    <circle
                        cx="100" cy="100" r="78"
                        fill="none" stroke="url(#arcFade)" strokeWidth="1.5"
                        strokeDasharray="90 400" strokeLinecap="round"
                    />
                    <circle
                        cx="100" cy="100" r="78"
                        fill="none" stroke="url(#arcFade)" strokeWidth="1.5"
                        strokeDasharray="50 400" strokeDashoffset="-200" strokeLinecap="round"
                    />
                </g>

                <g className="animate-spin-reverse" style={{ transformOrigin: '100px 100px' }}>
                    <circle
                        cx="100" cy="100" r="64"
                        fill="none" stroke={config.ring} strokeWidth="0.8"
                        strokeDasharray="6 14" opacity="0.5"
                    />
                </g>

                {/* Static frame */}
                <circle cx="100" cy="100" r="52" fill="none" stroke={config.ring} strokeWidth="0.6" opacity="0.28" />
                <circle cx="100" cy="100" r="88" fill="none" stroke={config.ring} strokeWidth="0.6" opacity="0.18" />

                {/* Reactive core */}
                <g
                    style={{
                        transformOrigin: '100px 100px',
                        transform: `scale(${coreScale})`,
                        transition: 'transform 90ms linear',
                    }}
                >
                    <circle cx="100" cy="100" r="42" fill="url(#coreFill)" opacity={glowOpacity} />
                    <circle
                        cx="100" cy="100" r="26"
                        fill="none" stroke={config.ring} strokeWidth="1.6"
                        opacity="0.85"
                        className={state === 'speaking' ? 'animate-pulse-ring' : ''}
                        style={{ transformOrigin: '100px 100px' }}
                    />
                    <circle cx="100" cy="100" r="9" fill={config.ring} opacity="0.95" />
                </g>

                {/* Thinking indicator: a bead tracking the ring */}
                {state === 'thinking' && (
                    <g className="animate-spin" style={{ transformOrigin: '100px 100px' }}>
                        <circle cx="100" cy="22" r="3.5" fill={config.ring} />
                    </g>
                )}
            </svg>

            {showLabel && (
                <div className="pointer-events-none absolute inset-x-0 -bottom-1 text-center">
                    <span
                        className="font-mono text-[10px] uppercase tracking-[0.35em] transition-colors duration-500"
                        style={{ color: config.ring, opacity: 0.85 }}
                    >
                        {config.label}
                    </span>
                </div>
            )}
        </div>
    )
}

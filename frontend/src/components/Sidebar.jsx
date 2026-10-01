import { NavLink } from 'react-router-dom'
import {
    BookOpen, Bot, BrainCircuit, CalendarRange, Compass, FlaskConical, Microscope, Plug, Radar, Search, Sigma,
    Sparkles, Terminal, TrendingUp, Wallet,
} from 'lucide-react'
import CoreOrb from './hud/CoreOrb'

/**
 * Navigation, grouped by what you're trying to do rather than by feature name.
 */

const SECTIONS = [
    {
        label: 'Command',
        items: [
            { path: '/', icon: Terminal, label: 'Command Deck', end: true },
            { path: '/wealth', icon: Wallet, label: 'Wealth' },
            { path: '/signals', icon: Radar, label: 'Watchtower' },
            { path: '/analyst', icon: Microscope, label: 'Analyst' },
        ],
    },
    {
        label: 'Trading',
        items: [
            { path: '/autopilot', icon: Bot, label: 'Autopilot' },
            { path: '/paper-trading', icon: FlaskConical, label: 'Paper Trading' },
            { path: '/long-term', icon: CalendarRange, label: 'Long-term picks' },
            { path: '/studio', icon: Sigma, label: 'Strategy Studio' },
        ],
    },
    {
        label: 'Research & learn',
        items: [
            // One entry: the lab's seven pages are tabs inside it.
            { path: '/research', icon: BrainCircuit, label: 'Research Lab' },
            { path: '/discover', icon: Compass, label: 'Discover' },
            { path: '/learn', icon: BookOpen, label: 'Learn' },
            { path: '/alerts', icon: TrendingUp, label: 'Alerts' },
        ],
    },
    {
        label: 'More',
        items: [
            // Experimental tools and manual holdings, on one page.
            { path: '/labs', icon: Sparkles, label: 'More tools' },
        ],
    },
]

export default function Sidebar({ brainState = 'idle', health }) {
    return (
        <aside className="flex w-60 shrink-0 flex-col border-r border-primary-400/10 bg-dark-950/60 backdrop-blur-xl">
            {/* Identity */}
            <div className="flex items-center gap-3 border-b border-primary-400/10 px-4 py-4">
                <CoreOrb state={brainState} size={40} showLabel={false} />
                <div className="min-w-0">
                    <h1 className="font-mono text-sm uppercase tracking-[0.3em] text-primary-300 text-glow">
                        Tradeo
                    </h1>
                    <p className="truncate font-mono text-[9px] uppercase tracking-[0.15em] text-dark-500">
                        Multi-asset intelligence
                    </p>
                </div>
            </div>

            <nav className="mask-fade-y flex-1 space-y-5 overflow-y-auto px-3 py-4">
                {SECTIONS.map((section) => (
                    <div key={section.label}>
                        <div className="mb-1.5 flex items-center gap-1.5 px-3">
                            {section.accent && <Sparkles className="h-3 w-3 text-alert-400" />}
                            <span
                                className={`font-mono text-[9px] uppercase tracking-[0.25em] ${
                                    section.accent ? 'text-alert-400/80' : 'text-dark-600'
                                }`}
                            >
                                {section.label}
                            </span>
                        </div>
                        <div className="space-y-0.5">
                            {section.items.map((item) => (
                                <NavLink
                                    key={item.path}
                                    to={item.path}
                                    end={item.end}
                                    className={({ isActive }) =>
                                        `group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-all duration-200 ${
                                            isActive
                                                ? 'bg-primary-500/10 text-primary-200'
                                                : 'text-dark-400 hover:bg-dark-800/50 hover:text-dark-100'
                                        }`
                                    }
                                >
                                    {({ isActive }) => (
                                        <>
                                            {isActive && (
                                                <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-primary-400 shadow-glow" />
                                            )}
                                            <item.icon className="h-4 w-4 shrink-0" />
                                            <span className="truncate">{item.label}</span>
                                        </>
                                    )}
                                </NavLink>
                            ))}
                        </div>
                    </div>
                ))}
            </nav>

            {/* Subsystem readout */}
            <div className="space-y-2 border-t border-primary-400/10 px-3 py-3">
                <NavLink
                    to="/setup"
                    className={({ isActive }) =>
                        `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                            isActive ? 'bg-primary-500/10 text-primary-200' : 'text-dark-400 hover:text-dark-100'
                        }`
                    }
                >
                    <Plug className="h-4 w-4" />
                    Connections
                </NavLink>

                <div className="space-y-1 rounded-md border border-dark-800/70 px-3 py-2">
                    <Readout label="Local" ok={health?.ai?.local} />
                    <Readout label="Cloud" ok={health?.ai?.cloud} optional />
                    <Readout label="Research" ok={health?.research?.online} optional />
                    <Readout label="Market" ok={health?.market_open} optional />
                </div>

                {/* Discoverability for the palette. A keyboard shortcut nobody
                    knows about is a shortcut nobody uses. */}
                <button
                    onClick={() =>
                        window.dispatchEvent(
                            new KeyboardEvent('keydown', { key: 'k', metaKey: true })
                        )
                    }
                    className="press flex w-full items-center justify-between rounded-md border border-dark-800/70 px-3 py-1.5 text-dark-500 transition-colors hover:border-primary-400/30 hover:text-primary-300"
                >
                    <span className="flex items-center gap-2 text-xs">
                        <Search className="h-3 w-3" />
                        Jump to…
                    </span>
                    <kbd className="font-mono text-[9px] tracking-wider">⌘K</kbd>
                </button>
            </div>
        </aside>
    )
}

function Readout({ label, ok, optional }) {
    return (
        <div className="flex items-center justify-between">
            <span className="font-mono text-[9px] uppercase tracking-[0.15em] text-dark-600">
                {label}
            </span>
            <span
                className={`h-1.5 w-1.5 rounded-full ${
                    ok ? 'bg-success-400 shadow-glow' : optional ? 'bg-dark-700' : 'bg-danger-400'
                }`}
            />
        </div>
    )
}

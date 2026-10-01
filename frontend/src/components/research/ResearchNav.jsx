import { useCallback, useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import {
    AlertTriangle, BrainCircuit, CalendarClock, FlaskConical, Grid3x3, History,
    Loader2, Network, RefreshCw, Sigma,
} from 'lucide-react'
import { researchApi } from '../../services/api'

/**
 * The research lab's shared header: one tab per engine feature, and the
 * engine's state (up, which model, whether it needs a restart) always in view.
 */

export const RESEARCH_TABS = [
    { to: '/research', label: 'Agent', icon: BrainCircuit, end: true },
    { to: '/research/swarm', label: 'Swarm', icon: Network },
    { to: '/research/alpha', label: 'Alpha Zoo', icon: Sigma },
    { to: '/research/options', label: 'Options Lab', icon: FlaskConical },
    { to: '/research/correlation', label: 'Correlation', icon: Grid3x3 },
    { to: '/research/schedules', label: 'Schedules', icon: CalendarClock },
    { to: '/research/runs', label: 'Runs', icon: History },
]

export function useEngine(pollMs = 15000) {
    const [engine, setEngine] = useState(null)
    const load = useCallback(async () => {
        try {
            setEngine((await researchApi.status()).data)
        } catch {
            setEngine({ online: false, error: 'Tradeo backend unreachable' })
        }
    }, [])
    useEffect(() => {
        load()
        const timer = setInterval(load, pollMs)
        return () => clearInterval(timer)
    }, [load, pollMs])
    return [engine, load]
}

export default function ResearchNav({ title, subtitle, engine, onEngineChange, actions }) {
    const [restarting, setRestarting] = useState(false)

    const restart = async () => {
        setRestarting(true)
        try {
            await researchApi.restart()
        } finally {
            // The engine takes ~30s to import; poll until it answers.
            for (let i = 0; i < 30; i += 1) {
                await new Promise((r) => setTimeout(r, 2000))
                try {
                    if ((await researchApi.status()).data.online) break
                } catch {
                    /* still starting */
                }
            }
            setRestarting(false)
            onEngineChange?.()
        }
    }

    const online = engine?.online
    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
                <div className="min-w-0">
                    <div className="hud-label text-primary-400/80">Research Lab</div>
                    <h1 className="mt-0.5 text-2xl font-semibold text-dark-50">{title}</h1>
                    {subtitle && <p className="mt-1 max-w-3xl text-sm text-dark-400">{subtitle}</p>}
                </div>
                <div className="flex items-center gap-2">
                    {actions}
                    <span
                        className={`flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-wider ${
                            online
                                ? 'border-success-400/30 bg-success-500/10 text-success-300'
                                : 'border-danger-400/30 bg-danger-500/10 text-danger-300'
                        }`}
                        title={engine?.error || ''}
                    >
                        <span className={`h-1.5 w-1.5 rounded-full ${online ? 'bg-success-400 animate-pulse' : 'bg-danger-400'}`} />
                        {online ? `${engine.llm?.provider} · ${engine.llm?.model}` : restarting ? 'starting' : 'engine offline'}
                    </span>
                    <button
                        onClick={restart}
                        disabled={restarting}
                        className="press rounded-full border border-dark-700 p-1.5 text-dark-400 transition-colors hover:border-primary-400/40 hover:text-primary-300 disabled:opacity-50"
                        title="Restart the engine (picks up new AI settings)"
                    >
                        {restarting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                    </button>
                </div>
            </div>

            <nav className="mask-fade-x flex gap-1 overflow-x-auto rounded-lg border border-primary-400/10 bg-dark-950/50 p-1">
                {RESEARCH_TABS.map((tab) => (
                    <NavLink
                        key={tab.to}
                        to={tab.to}
                        end={tab.end}
                        className={({ isActive }) =>
                            `press flex shrink-0 items-center gap-2 rounded-md px-3 py-1.5 text-xs transition-all duration-200 ${
                                isActive
                                    ? 'bg-primary-500/15 text-primary-200 shadow-glow'
                                    : 'text-dark-400 hover:bg-dark-800/60 hover:text-dark-100'
                            }`
                        }
                    >
                        <tab.icon className="h-3.5 w-3.5" />
                        {tab.label}
                    </NavLink>
                ))}
            </nav>

            {engine?.needs_restart && (
                <Banner>
                    AI settings changed to {engine.configured_llm.provider} · {engine.configured_llm.model}. Restart the
                    engine (↻) to use them.
                </Banner>
            )}
            {engine && !engine.online && engine.error && !restarting && <Banner tone="danger">{engine.error}</Banner>}
        </div>
    )
}

export function Banner({ children, tone = 'alert' }) {
    const cls = tone === 'danger'
        ? 'border-danger-400/30 bg-danger-500/10 text-danger-300'
        : 'border-alert-400/30 bg-alert-500/10 text-alert-300'
    return (
        <div className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs ${cls}`}>
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <div>{children}</div>
        </div>
    )
}

/** Shown on screens that need the LLM when it is the slow local model. */
export function LocalModelNote({ engine }) {
    if (engine?.llm?.provider !== 'ollama') return null
    return (
        <Banner>
            Running on the local model: replies take minutes and a 3B model loses track of tools. Add a cloud key
            under <Link to="/setup" className="underline">Connections</Link>, then restart the engine.
        </Banner>
    )
}

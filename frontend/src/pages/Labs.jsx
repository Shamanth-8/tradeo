import { Link } from 'react-router-dom'
import { Activity, Dna, LayoutDashboard, LogOut, Rocket, Shield, Undo2, Users } from 'lucide-react'

/**
 * Extra tools, one page instead of eight sidebar entries. Experimental
 * features live here; the main workflow is in the sidebar.
 */

const TOOLS = [
    { path: '/novel/margin', icon: Shield, label: 'Margin of Safety', what: 'Fair value from several methods, and how far the price is below it.' },
    { path: '/novel/exit', icon: LogOut, label: 'Exit Architect', what: 'Stop, targets and position size from the same fair value and volatility.' },
    { path: '/novel/mood', icon: Activity, label: 'Market Mood', what: 'Breadth, volatility and news tone in one reading of the market.' },
    { path: '/novel/regret', icon: Undo2, label: 'Regret Analyzer', what: 'What your past trades would have done if held longer or exited earlier.' },
    { path: '/novel/trade-clone', icon: Users, label: 'Trade Clone', what: 'Track investor-style portfolios (Jhunjhunwala, Buffett-style, quality index) and post-mortems.' },
    { path: '/novel/dna', icon: Dna, label: 'Stock DNA', what: 'Find stocks that behave like one you already understand.' },
    { path: '/novel/future', icon: Rocket, label: 'Future You', what: 'Project where regular investing could take you.' },
    { path: '/portfolio', icon: LayoutDashboard, label: 'Manual holdings', what: 'Enter holdings by hand; they appear in Wealth and the Real portfolio.' },
]

export default function Labs() {
    return (
        <div className="space-y-5">
            <header>
                <h1 className="flex items-center gap-3 font-mono text-lg uppercase tracking-[0.3em] text-primary-300 text-glow">
                    More tools
                    <span className="rounded border border-alert-400/40 px-1.5 py-0.5 text-[10px] tracking-[0.15em] text-alert-300">Experimental</span>
                </h1>
                <p className="mt-1 text-xs text-dark-400">
                    Experimental extras: less tested than the core, and their outputs are rough heuristics, not
                    evidence. They may change or be removed. The core workflow — Command Deck, Wealth, Watchtower,
                    Analyst, Autopilot and Paper Trading — is in the sidebar.
                </p>
            </header>
            <div className="stagger grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {TOOLS.map(({ path, icon: Icon, label, what }) => (
                    <Link key={path} to={path}
                        className="rounded-lg border border-dark-700/70 bg-dark-900/40 p-4 transition hover:border-primary-400/40 hover:bg-primary-500/5">
                        <Icon className="h-5 w-5 text-primary-400" />
                        <p className="mt-2 font-semibold text-white">{label}</p>
                        <p className="mt-1 text-xs text-dark-400">{what}</p>
                    </Link>
                ))}
            </div>
        </div>
    )
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    Activity, ArrowRight, BookOpen, Bot, BrainCircuit, CalendarClock, Grid3x3, History, Network, Sigma, Compass, CornerDownLeft, Dna,
    FlaskConical, LayoutDashboard, LogOut, Microscope, Plug, Radar, Rocket,
    Search, Shield, Sparkles, Terminal, TrendingUp, Undo2, Users, Wallet,
} from 'lucide-react'
import { strategyApi } from '../../services/api'

/**
 * ⌘K.
 *
 * An app with twenty-odd screens and a sidebar that has to scroll has already
 * outgrown navigation-by-hunting. The palette is the fast path: type three
 * letters, hit enter.
 *
 * Two behaviours are load-bearing and easy to get wrong. A bare uppercase word
 * that matches nothing is treated as a ticker, so "IRFC" goes straight to that
 * instrument rather than returning nothing — a search box that can only find
 * things it has a list of is useless in a market app. And saved strategies are
 * fetched once when the palette first opens, not on every keystroke, so typing
 * never waits on the network.
 */

const ROUTES = [
    { path: '/', label: 'Command Deck', icon: Terminal, keywords: 'home talk voice ask assistant' },
    { path: '/wealth', label: 'Wealth', icon: Wallet, keywords: 'portfolio holdings consolidated net worth' },
    { path: '/signals', label: 'Watchtower', icon: Radar, keywords: 'scanner opportunities alerts signals' },
    { path: '/analyst', label: 'Analyst', icon: Microscope, keywords: 'analysis charts evidence reasoning' },
    { path: '/studio', label: 'Strategy Studio', icon: FlaskConical, keywords: 'code backtest walk forward sweep optimise custom python' },
    { path: '/research', label: 'Research Agent', icon: BrainCircuit, keywords: 'agent llm research backtest strategy vibe quant' },
    { path: '/research/swarm', label: 'Agent Swarm', icon: Network, keywords: 'team multi agent research report' },
    { path: '/research/alpha', label: 'Alpha Zoo', icon: Sigma, keywords: 'factors alpha101 ic bench quant' },
    { path: '/research/options', label: 'Options Lab', icon: FlaskConical, keywords: 'payoff greeks strategy spread straddle nifty' },
    { path: '/research/correlation', label: 'Correlation', icon: Grid3x3, keywords: 'heatmap diversification regime pairs' },
    { path: '/research/schedules', label: 'Research Schedules', icon: CalendarClock, keywords: 'cron playbook automatic daily' },
    { path: '/research/runs', label: 'Research Runs', icon: History, keywords: 'agent backtest results equity curve sharpe' },
    { path: '/autopilot', label: 'Autopilot', icon: Bot, keywords: 'proposals guardrails paper trading execution' },
    { path: '/discover', label: 'Discover', icon: Compass, keywords: 'suitability recommendations reits bonds gsec' },
    { path: '/learn', label: 'Learn', icon: BookOpen, keywords: 'lessons education failure modes' },
    { path: '/portfolio', label: 'Manual Book', icon: LayoutDashboard, keywords: 'manual entries positions' },
    { path: '/paper-trading', label: 'Paper Trading', icon: FlaskConical, keywords: 'virtual practice' },
    { path: '/alerts', label: 'Alerts', icon: TrendingUp, keywords: 'price triggers notifications' },
    { path: '/setup', label: 'Connections', icon: Plug, keywords: 'brokers api keys credentials telegram setup' },
    { path: '/novel/trade-clone', label: 'Trade Clone', icon: Users, keywords: 'novel mirror' },
    { path: '/novel/regret', label: 'Regret Analyzer', icon: Undo2, keywords: 'novel what if' },
    { path: '/novel/mood', label: 'Market Mood', icon: Activity, keywords: 'novel sentiment ring' },
    { path: '/novel/dna', label: 'Stock DNA', icon: Dna, keywords: 'novel similarity matching' },
    { path: '/novel/future', label: 'Future You', icon: Rocket, keywords: 'novel projection simulator' },
    { path: '/novel/margin', label: 'Margin of Safety', icon: Shield, keywords: 'novel valuation' },
    { path: '/novel/exit', label: 'Exit Architect', icon: LogOut, keywords: 'novel selling plan' },
]

/**
 * Subsequence match, the way every good palette works.
 *
 * "swf" finds "Strategy Walk-Forward" because the letters appear in order.
 * Exact substring matches score far higher so they always sort first — the
 * fuzzy fallback is there to catch what you half-remember, not to bury what
 * you typed correctly.
 */
function score(query, text) {
    if (!query) return 1
    const haystack = text.toLowerCase()
    const needle = query.toLowerCase()

    const direct = haystack.indexOf(needle)
    if (direct === 0) return 1000
    if (direct > 0) return 700 - direct

    let index = 0
    let matched = 0
    let streak = 0
    let best = 0
    for (const char of haystack) {
        if (index < needle.length && char === needle[index]) {
            index += 1
            matched += 1
            streak += 1
            best = Math.max(best, streak)
        } else {
            streak = 0
        }
    }
    return index === needle.length ? 100 + matched * 4 + best * 6 : 0
}

export default function CommandPalette() {
    const navigate = useNavigate()
    const [open, setOpen] = useState(false)
    const [query, setQuery] = useState('')
    const [cursor, setCursor] = useState(0)
    const [strategies, setStrategies] = useState([])
    const [loaded, setLoaded] = useState(false)
    const inputRef = useRef(null)
    const listRef = useRef(null)

    // ---- open / close ----------------------------------------------------
    useEffect(() => {
        const onKey = (event) => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
                event.preventDefault()
                setOpen((value) => !value)
                return
            }
            if (event.key === 'Escape') setOpen(false)
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
    }, [])

    useEffect(() => {
        if (!open) {
            setQuery('')
            setCursor(0)
            return
        }
        requestAnimationFrame(() => inputRef.current?.focus())
        if (loaded) return
        strategyApi
            .list()
            .then(({ data }) => setStrategies(data.strategies || []))
            .catch(() => setStrategies([]))
            .finally(() => setLoaded(true))
    }, [open, loaded])

    // ---- results ---------------------------------------------------------
    const results = useMemo(() => {
        const items = [
            ...ROUTES.map((route) => ({
                kind: 'Go to',
                id: route.path,
                label: route.label,
                icon: route.icon,
                haystack: `${route.label} ${route.keywords}`,
                run: () => navigate(route.path),
            })),
            ...strategies.map((strategy) => ({
                kind: 'Strategy',
                id: `strategy-${strategy.id}`,
                label: strategy.name,
                icon: FlaskConical,
                detail: strategy.deployed ? 'deployed' : '',
                haystack: `${strategy.name} strategy ${strategy.category || ''}`,
                run: () => navigate(`/studio?strategy=${strategy.id}`),
            })),
        ]

        const scored = items
            .map((item) => ({ item, value: score(query, item.haystack) }))
            .filter((entry) => entry.value > 0)
            .sort((a, b) => b.value - a.value)
            .slice(0, 9)
            .map((entry) => entry.item)

        // A ticker-shaped query always offers the instrument, even when it
        // also matched a screen name.
        const ticker = query.trim().toUpperCase()
        if (/^[A-Z][A-Z0-9&-]{1,19}$/.test(ticker)) {
            scored.unshift({
                kind: 'Instrument',
                id: `symbol-${ticker}`,
                label: ticker,
                icon: Search,
                detail: 'open instrument',
                run: () => navigate(`/stock/${ticker}`),
            })
            scored.push({
                kind: 'Instrument',
                id: `studio-${ticker}`,
                label: `Backtest on ${ticker}`,
                icon: FlaskConical,
                detail: 'strategy studio',
                run: () => navigate(`/studio?symbol=${ticker}`),
            })
        }

        return scored.slice(0, 10)
    }, [query, strategies, navigate])

    useEffect(() => setCursor(0), [query])

    const choose = useCallback(
        (item) => {
            if (!item) return
            setOpen(false)
            item.run()
        },
        []
    )

    const onKeyDown = useCallback(
        (event) => {
            if (event.key === 'ArrowDown') {
                event.preventDefault()
                setCursor((value) => Math.min(value + 1, results.length - 1))
            } else if (event.key === 'ArrowUp') {
                event.preventDefault()
                setCursor((value) => Math.max(value - 1, 0))
            } else if (event.key === 'Enter') {
                event.preventDefault()
                choose(results[cursor])
            }
        },
        [results, cursor, choose]
    )

    // Keep the highlighted row in view when arrowing past the fold.
    useEffect(() => {
        listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
    }, [cursor])

    if (!open) return null

    return (
        <div
            className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-[12vh]"
            onMouseDown={(event) => event.target === event.currentTarget && setOpen(false)}
        >
            <div className="absolute inset-0 bg-dark-950/70 backdrop-blur-sm" />

            <div className="materialize material-panel relative w-full max-w-xl overflow-hidden rounded-xl border-primary-400/25 shadow-glow-lg">
                <div className="flex items-center gap-3 border-b border-primary-400/12 px-4 py-3">
                    <Sparkles className="h-4 w-4 shrink-0 text-primary-400" />
                    <input
                        ref={inputRef}
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        onKeyDown={onKeyDown}
                        placeholder="Go to a screen, open a ticker, load a strategy…"
                        className="flex-1 bg-transparent font-mono text-sm text-dark-50 placeholder-dark-500 outline-none"
                    />
                    <kbd className="rounded border border-dark-600/70 px-1.5 py-0.5 font-mono text-[9px] text-dark-500">
                        ESC
                    </kbd>
                </div>

                <div ref={listRef} className="max-h-[52vh] overflow-y-auto p-1.5">
                    {results.length === 0 ? (
                        <p className="px-3 py-6 text-center text-xs text-dark-500">
                            Nothing matches “{query}”.
                        </p>
                    ) : (
                        results.map((item, index) => {
                            const active = index === cursor
                            const Icon = item.icon
                            return (
                                <button
                                    key={item.id}
                                    data-active={active}
                                    onMouseEnter={() => setCursor(index)}
                                    onClick={() => choose(item)}
                                    className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors ${
                                        active ? 'bg-primary-500/12' : 'hover:bg-dark-800/50'
                                    }`}
                                >
                                    <Icon
                                        className={`h-4 w-4 shrink-0 ${
                                            active ? 'text-primary-300' : 'text-dark-500'
                                        }`}
                                    />
                                    <span
                                        className={`min-w-0 flex-1 truncate text-sm ${
                                            active ? 'text-dark-50' : 'text-dark-200'
                                        }`}
                                    >
                                        {item.label}
                                    </span>
                                    {item.detail && (
                                        <span className="shrink-0 font-mono text-[10px] text-dark-600">
                                            {item.detail}
                                        </span>
                                    )}
                                    <span className="shrink-0 font-mono text-[9px] uppercase tracking-widest text-dark-600">
                                        {item.kind}
                                    </span>
                                    {active ? (
                                        <CornerDownLeft className="h-3 w-3 shrink-0 text-primary-400" />
                                    ) : (
                                        <ArrowRight className="h-3 w-3 shrink-0 text-transparent" />
                                    )}
                                </button>
                            )
                        })
                    )}
                </div>

                <div className="flex items-center gap-4 border-t border-primary-400/10 px-4 py-2 font-mono text-[9px] uppercase tracking-widest text-dark-600">
                    <span>↑↓ navigate</span>
                    <span>↵ open</span>
                    <span className="ml-auto">⌘K toggle</span>
                </div>
            </div>
        </div>
    )
}

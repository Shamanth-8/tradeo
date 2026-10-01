import { useCallback, useEffect, useRef, useState } from 'react'
import {
    Activity, Brain, Check, ChevronDown, Clock, GitBranch, Play, ShieldCheck, X, Zap,
} from 'lucide-react'
import DecisionChart from '../components/agents/DecisionChart'
import ThoughtRow from '../components/agents/Thought'
import { useSpringValue, SPRING } from '../hooks/useSpring'
import { agentsApi } from '../services/api'

/**
 * The analyst console.
 *
 * Design intent: this should feel like a system thinking out loud in front of
 * you, on glass, in a dark room — not a form that returns a result. Concretely
 * that means the reasoning arrives progressively and materializes, numbers
 * settle rather than snap, and every surface is a translucent layer with real
 * depth rather than a bordered rectangle.
 *
 * Two things it is careful *not* to do:
 *   - imply the machine decided anything (the score is labelled a reference
 *     reading, and contradictions are given their own surface rather than
 *     being buried), and
 *   - move for the sake of moving. Motion is used where it carries meaning:
 *     arrival, settling, and waiting for a human.
 */

const READING = {
    strong_buy: { label: 'Strong Buy', tone: 'text-success-300', glow: 'rgba(74,222,128,0.35)' },
    buy: { label: 'Buy', tone: 'text-success-400', glow: 'rgba(74,222,128,0.28)' },
    hold: { label: 'Hold', tone: 'text-dark-200', glow: 'rgba(148,163,184,0.22)' },
    avoid: { label: 'Avoid', tone: 'text-alert-300', glow: 'rgba(251,191,36,0.3)' },
    sell: { label: 'Sell', tone: 'text-danger-300', glow: 'rgba(248,113,113,0.32)' },
}

const KIND_TONE = {
    compute: 'text-primary-300/90 border-primary-400/25',
    reason: 'text-violet-300/90 border-violet-400/25',
    verify: 'text-blue-300/90 border-blue-400/25',
    decide: 'text-success-300/90 border-success-400/25',
    act: 'text-danger-300/90 border-danger-400/25',
}

/**
 * A number that settles into place instead of snapping.
 *
 * Coerced defensively. A partial run legitimately has fields that are not
 * computed yet, and a display component must degrade to a dash rather than
 * take the page down with it.
 */
function safeNumber(value, fallback = 0) {
    const n = Number(value)
    return Number.isFinite(n) ? n : fallback
}

function SpringNumber({ value, decimals = 0, suffix = '' }) {
    const animated = useSpringValue(safeNumber(value), SPRING.gentle)
    return (
        <span className="type-numeric">
            {safeNumber(animated).toFixed(decimals)}
            {suffix}
        </span>
    )
}

/**
 * Conviction, as a ring that fills.
 *
 * Sized so the number inside is genuinely readable — an earlier 76px version
 * had to shrink the digits to ~11px and stack a label under them, which made
 * the most precise figure on the screen the hardest one to read. The ring is
 * the label; it does not need a second one inside it.
 */
function ConvictionRing({ value, tone }) {
    const animated = safeNumber(useSpringValue(safeNumber(value), SPRING.gentle))
    const size = 104
    const radius = 44
    const circumference = 2 * Math.PI * radius
    const offset = circumference * (1 - Math.max(0, Math.min(100, animated)) / 100)

    return (
        <div className="relative shrink-0" style={{ height: size, width: size }}>
            <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full -rotate-90">
                <circle
                    cx={size / 2} cy={size / 2} r={radius} fill="none"
                    stroke="rgba(148,163,184,0.12)" strokeWidth="2.5"
                />
                <circle
                    cx={size / 2} cy={size / 2} r={radius} fill="none"
                    stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
                    strokeDasharray={circumference} strokeDashoffset={offset}
                    className={tone}
                    style={{ filter: 'drop-shadow(0 0 6px currentColor)' }}
                />
            </svg>
            <div className="absolute inset-0 flex items-center justify-center">
                <span className={`type-numeric text-[30px] font-medium leading-none ${tone}`}>
                    {animated.toFixed(0)}
                    <span className="ml-0.5 align-top text-sm opacity-55">%</span>
                </span>
            </div>
        </div>
    )
}

export default function Analyst() {
    const [agents, setAgents] = useState([])
    const [agent, setAgent] = useState('thesis')
    const [symbol, setSymbol] = useState('TCS')
    const [deep, setDeep] = useState(false)
    const [thoughts, setThoughts] = useState([])
    const [run, setRun] = useState(null)
    const [running, setRunning] = useState(false)
    const [error, setError] = useState(null)
    const [autoScroll, setAutoScroll] = useState(true)

    const streamRef = useRef(null)
    const logRef = useRef(null)
    const seenRef = useRef(0)

    useEffect(() => {
        agentsApi.list().then(({ data }) => setAgents(data.agents || [])).catch(() => {})
        return () => streamRef.current?.close()
    }, [])

    // Follow the stream, but stop fighting the user the moment they scroll up
    // to read something. Auto-scroll that cannot be escaped is hostile.
    useEffect(() => {
        if (!autoScroll || !logRef.current) return
        logRef.current.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
    }, [thoughts, autoScroll])

    const onScroll = useCallback((event) => {
        const el = event.currentTarget
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48
        setAutoScroll(atBottom)
    }, [])

    const start = useCallback(async () => {
        if (running || !symbol.trim()) return
        setThoughts([])
        setRun(null)
        setError(null)
        setRunning(true)
        setAutoScroll(true)
        seenRef.current = 0
        streamRef.current?.close()

        try {
            const { data } = await agentsApi.run(agent, symbol.trim().toUpperCase(), deep)
            const source = agentsApi.stream(data.run_id)
            streamRef.current = source

            source.onmessage = (event) => {
                const frame = JSON.parse(event.data)
                if (frame.type === 'thought') {
                    setThoughts((prev) => [...prev, frame])
                } else if (frame.type === 'approval_required') {
                    setRun(frame.run)
                } else if (frame.type === 'done') {
                    setRun(frame.run)
                    setRunning(false)
                    source.close()
                } else if (frame.type === 'error') {
                    setError(frame.error)
                    setRunning(false)
                    source.close()
                }
            }
            source.onerror = () => {
                setRunning(false)
                source.close()
            }
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
            setRunning(false)
        }
    }, [agent, symbol, deep, running])

    const respond = useCallback(async (approved) => {
        if (!run?.id) return
        try {
            await agentsApi.approve(run.id, approved, approved ? '' : 'declined from console')
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        }
    }, [run])

    const selected = agents.find((a) => a.name === agent)
    const awaiting = run?.state === 'awaiting_approval'
    // An empty object is truthy, so `run.verdict &&` is not enough — a paused
    // run used to render the reading card with every field undefined.
    const verdict = run?.verdict?.verdict ? run.verdict : null
    const proposal = run?.steps?.find((s) => s.id === 'propose')?.evidence?.[0]?.values
    const conflicts = thoughts.filter((t) => t.level === 'conflict' || t.level === 'warn')
    const reading = READING[verdict?.verdict] || READING.hold

    return (
        <div className="flex h-full flex-col gap-3 p-4">
            {/* ---- control bar: floating chrome, not a fixed strip ---- */}
            <header className="material-panel flex flex-wrap items-center gap-2.5 rounded-xl px-3 py-2.5">
                <div className="relative">
                    <select
                        value={agent}
                        onChange={(e) => setAgent(e.target.value)}
                        className="material-control press type-body vibrant cursor-pointer appearance-none rounded-lg py-1.5 pl-3 pr-8 outline-none focus:border-primary-400/50"
                    >
                        {agents.map((a) => (
                            <option key={a.name} value={a.name} className="bg-dark-900">
                                {a.title}
                            </option>
                        ))}
                    </select>
                    <ChevronDown
                        size={13}
                        className="vibrant-tertiary pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2"
                    />
                </div>

                <input
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                    onKeyDown={(e) => e.key === 'Enter' && start()}
                    placeholder="SYMBOL"
                    spellCheck={false}
                    className="material-control type-numeric vibrant w-28 rounded-lg px-3 py-1.5 font-mono text-sm uppercase outline-none placeholder:text-dark-500 focus:border-primary-400/50"
                />

                <button
                    onClick={() => setDeep((d) => !d)}
                    title="Adds narrative commentary from the local model. ~40s instead of ~5s — and the model is commentary, not authority."
                    className={`press type-caption rounded-lg px-2.5 py-1.5 ${
                        deep
                            ? 'material-control text-violet-200'
                            : 'vibrant-tertiary border border-transparent hover:text-dark-300'
                    }`}
                >
                    Deep
                    <span className="ml-1.5 text-[10px] opacity-60">+40s</span>
                </button>

                <button
                    onClick={start}
                    disabled={running}
                    className="press material-control ml-auto flex items-center gap-1.5 rounded-lg border-primary-400/30 bg-primary-500/15 px-3.5 py-1.5 text-primary-100 disabled:opacity-50"
                >
                    {running
                        ? <Activity size={13} className="animate-pulse" />
                        : <Play size={13} fill="currentColor" />}
                    <span className="type-body">{running ? 'Analysing' : 'Analyse'}</span>
                </button>
            </header>

            {selected && (
                <p className="type-caption vibrant-tertiary -mt-1 max-w-4xl px-1">
                    {selected.description}
                </p>
            )}

            {error && (
                <div className="material-panel type-body rounded-lg border-danger-500/40 px-3 py-2 text-danger-200">
                    {error}
                </div>
            )}

            {/* ---- approval gate ----
                 Given its own full-width surface with a distinct material.
                 Something waiting on a human should not have to compete for
                 attention with a chart. */}
            {awaiting && proposal && (
                <div className="material-attention materialize rounded-xl p-4">
                    <div className="mb-3 flex items-center gap-2 text-alert-200">
                        <ShieldCheck size={15} />
                        <span className="type-label">Awaiting your approval</span>
                    </div>

                    <p className="type-title vibrant mb-1">
                        <span className={proposal.side === 'BUY' ? 'text-success-300' : 'text-danger-300'}>
                            {proposal.side}
                        </span>{' '}
                        <span className="type-numeric">{proposal.quantity}</span> ×{' '}
                        {proposal.symbol}
                        <span className="vibrant-secondary type-body">
                            {' '}at ₹{Number(proposal.entry).toLocaleString('en-IN')}
                        </span>
                    </p>

                    <p className="type-body vibrant-secondary mb-4">
                        ₹{Number(proposal.capital).toLocaleString('en-IN')} — {proposal.position_size_pct}% of
                        the book. If the stop at ₹{Number(proposal.stop_loss).toLocaleString('en-IN')} is
                        hit you lose{' '}
                        <span className="text-danger-300 type-numeric">
                            ₹{Number(proposal.max_loss).toLocaleString('en-IN')}
                        </span>{' '}
                        ({proposal.max_loss_pct_of_portfolio}% of the portfolio). Paper account —
                        nothing has been sent to a broker.
                    </p>

                    <div className="flex gap-2">
                        <button
                            onClick={() => respond(true)}
                            className="press material-control flex items-center gap-1.5 rounded-lg border-success-400/35 bg-success-500/20 px-3.5 py-1.5 text-success-100"
                        >
                            <Check size={14} /> <span className="type-body">Approve</span>
                        </button>
                        <button
                            onClick={() => respond(false)}
                            className="press material-control flex items-center gap-1.5 rounded-lg border-danger-400/30 bg-danger-500/15 px-3.5 py-1.5 text-danger-100"
                        >
                            <X size={14} /> <span className="type-body">Decline</span>
                        </button>
                    </div>
                </div>
            )}

            <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
                {/* ---- reasoning ---- */}
                <section className="material-panel flex min-h-0 flex-col overflow-hidden rounded-xl">
                    <div className="flex items-center gap-2 px-3.5 py-2.5">
                        <Brain size={13} className="text-violet-300/90" />
                        <span className="type-label vibrant-secondary">Reasoning</span>
                        {run && (
                            <span className="type-caption vibrant-tertiary ml-auto flex items-center gap-1">
                                <Clock size={10} />
                                <span className="type-numeric">
                                    {(run.duration_ms / 1000).toFixed(1)}s
                                </span>
                            </span>
                        )}
                    </div>

                    <div
                        ref={logRef}
                        onScroll={onScroll}
                        className="edge-fade-top edge-fade-bottom relative min-h-0 flex-1 space-y-2 overflow-y-auto px-3.5 pb-4 pt-2"
                    >
                        {thoughts.length === 0 && !running && (
                            <p className="type-body vibrant-tertiary py-12 text-center">
                                Run an analysis to watch it reason.
                            </p>
                        )}

                        {thoughts.map((thought, i) => {
                            // Only rows that arrived after the last render animate;
                            // replaying the whole list on every append would make
                            // the log shimmer.
                            const isNew = i >= seenRef.current
                            if (i === thoughts.length - 1) seenRef.current = thoughts.length
                            return <ThoughtRow key={i} thought={thought} isNew={isNew} />
                        })}

                        {running && (
                            <div className="type-caption vibrant-tertiary flex items-center gap-2 pl-3 pt-1">
                                <span className="relative flex h-1.5 w-1.5">
                                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary-400 opacity-75" />
                                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary-400" />
                                </span>
                                thinking
                            </div>
                        )}
                    </div>

                    {/* Step ledger — what ran, what it cost, what was skipped. */}
                    {run?.steps && (
                        <div className="flex flex-wrap gap-1 px-3 pb-3 pt-1">
                            {run.steps.map((step) => {
                                const done = step.status === 'done'
                                return (
                                    <span
                                        key={step.id}
                                        title={`${step.kind} · ${step.status}${
                                            step.skip_reason ? ` · ${step.skip_reason}` : ''
                                        }`}
                                        className={`type-caption rounded-md border px-1.5 py-0.5 text-[10px] ${
                                            done
                                                ? `material-control ${KIND_TONE[step.kind] || ''}`
                                                : 'vibrant-tertiary border-dark-700/60 opacity-45'
                                        }`}
                                    >
                                        {step.title}
                                        {done && (
                                            <span className="type-numeric ml-1 opacity-55">
                                                {step.duration_ms.toFixed(0)}ms
                                            </span>
                                        )}
                                    </span>
                                )
                            })}
                        </div>
                    )}
                </section>

                {/* ---- evidence ---- */}
                <section className="edge-fade-top relative min-h-0 space-y-3 overflow-y-auto rounded-xl pr-0.5">
                    {verdict && (
                        <div
                            className="material-panel materialize rounded-xl p-4"
                            style={{ boxShadow: `0 0 40px -18px ${reading.glow}` }}
                        >
                            <div className="flex items-center gap-5">
                                <ConvictionRing value={verdict.conviction} tone={reading.tone} />
                                <div className="min-w-0 flex-1">
                                    <p className="type-label vibrant-secondary">
                                        Reference reading
                                    </p>
                                    <p className={`type-display ${reading.tone}`}>
                                        {reading.label}
                                    </p>
                                    <p className="type-caption vibrant-tertiary mt-1">
                                        Scored from the evidence. Not a recommendation.
                                    </p>
                                </div>
                            </div>

                            <div className="mt-4 grid grid-cols-4 gap-1.5">
                                {[
                                    ['Entry', verdict.entry, '₹'],
                                    ['Stop', verdict.stop_loss, '₹'],
                                    ['R : R', verdict.risk_reward, ''],
                                    ['Size', verdict.position_size_pct, '%'],
                                ].map(([label, value, unit]) => (
                                    <div key={label} className="material-raised rounded-lg px-2 py-2 text-center">
                                        {/* Secondary, not tertiary: these labels are the only
                                            thing telling you what the numbers under them mean. */}
                                        <p className="type-label vibrant-secondary text-[9px]">{label}</p>
                                        <p className="type-numeric vibrant mt-0.5 text-[15px]">
                                            {unit === '₹' ? '₹' : ''}
                                            <SpringNumber
                                                value={Number(value) || 0}
                                                decimals={unit === '₹' ? 0 : 2}
                                            />
                                            {unit === '%' ? '%' : ''}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {conflicts.length > 0 && (
                        <div className="material-panel materialize rounded-xl border-alert-500/25 p-3.5">
                            <p className="type-label mb-2 flex items-center gap-1.5 text-alert-300">
                                <GitBranch size={12} />
                                Unresolved · {conflicts.length}
                            </p>
                            <ul className="space-y-1.5">
                                {conflicts.map((conflict, i) => (
                                    <li key={i} className="type-body vibrant-secondary flex gap-2">
                                        <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-alert-400/70" />
                                        {conflict.text}
                                    </li>
                                ))}
                            </ul>
                            <p className="type-caption vibrant-tertiary mt-2.5">
                                The score prices these in. It does not resolve them — that part is yours.
                            </p>
                        </div>
                    )}

                    {run?.charts?.map((chart, i) => (
                        <DecisionChart key={`${chart.kind}-${i}`} chart={chart} />
                    ))}

                    {!run && !running && (
                        <div className="material-panel flex h-full flex-col items-center justify-center gap-3 rounded-xl py-20 text-center">
                            <Zap size={20} className="text-dark-600" />
                            <p className="type-body vibrant-tertiary max-w-xs">
                                Charts explaining each reading appear here — including the waterfall
                                showing exactly how the conviction number was built.
                            </p>
                        </div>
                    )}
                </section>
            </div>
        </div>
    )
}

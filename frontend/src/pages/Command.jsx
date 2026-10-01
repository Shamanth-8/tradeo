import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    Activity, AlertTriangle, Mic, Radio, Send,
    Sparkles, Square, Volume2, VolumeX,
} from 'lucide-react'
import CoreOrb from '../components/hud/CoreOrb'
import { HudPanel, Stat, StatusDot, Delta, Empty, Loading } from '../components/hud/HudPanel'
import useVoice from '../hooks/useVoice'
import ResearchRail from '../components/research/ResearchRail'
import PortfolioCard from '../components/PortfolioCard'
import { aiApi, autopilotApi, signalsApi, wealthApi } from '../services/api'

/**
 * The command deck — where you talk to Tradeo.
 *
 * Voice and text share one transcript and one session, so switching between
 * them mid-conversation keeps the thread.
 */

const SUGGESTIONS = [
    'How is my portfolio doing?',
    "What's the sentiment on Reliance?",
    'Show me opportunities right now',
    'Explain REITs for my portfolio',
]

export default function Command() {
    const navigate = useNavigate()
    const [commands, setCommands] = useState([])
    const [messages, setMessages] = useState([])
    const [input, setInput] = useState('')
    const [busy, setBusy] = useState(false)
    const [autoSpeak, setAutoSpeak] = useState(true)
    const [brain, setBrain] = useState(null)
    const [automation, setAutomation] = useState(null)

    useEffect(() => {
        autopilotApi.agents().then(({ data }) => setAutomation(data)).catch(() => setAutomation([]))
    }, [])
    const [market, setMarket] = useState(null)
    const [totals, setTotals] = useState(null)
    const [live, setLive] = useState([])

    const scrollRef = useRef(null)
    const streamAbort = useRef(null)

    const push = useCallback((message) => {
        setMessages((prev) => [...prev, { ...message, at: Date.now() }])
    }, [])

    const voice = useVoice({
        autoSpeak,
        onTranscript: (text) => push({ role: 'user', text, via: 'voice' }),
        onAnswer: (data) =>
            push({
                role: 'assistant',
                text: data.response,
                symbols: data.symbols,
                meta: data.meta,
                action: data.action,
                link: data.navigate,
                via: 'voice',
            }),
        // Answers stay on the Command Deck; a command's page is offered as a
        // link in the reply instead of yanking the user away mid-conversation.
        onNavigate: () => {},
    })

    // Manual fallback: every voice command, as a button. Same endpoint, same
    // handler — so a command cannot work by voice and be broken by click.
    const runCommand = useCallback(
        async (text) => {
            if (busy) return
            push({ role: 'user', text, via: 'manual' })
            setBusy(true)
            try {
                const { data } = await aiApi.voiceCommand(text, true)
                push({
                    role: 'assistant',
                    text: data.response || data.speech,
                    action: data.action,
                    link: data.navigate,
                    via: 'manual',
                })
                if (autoSpeak && data.speech) voice.speak(data.speech)
            } catch (err) {
                push({
                    role: 'assistant',
                    text: err?.response?.data?.detail || err.message || 'Command failed',
                    error: true,
                    via: 'manual',
                })
            } finally {
                setBusy(false)
            }
        },
        [busy, push, autoSpeak, voice, navigate]
    )

    // --- Ambient status ---------------------------------------------------

    useEffect(() => {
        let alive = true
        const load = async () => {
            const [b, m, w, o] = await Promise.allSettled([
                aiApi.status(),
                signalsApi.market(),
                wealthApi.holdings(),
                signalsApi.opportunities({ limit: 5, since_hours: 48 }),
            ])
            if (!alive) return
            if (b.status === 'fulfilled') setBrain(b.value.data)
            if (m.status === 'fulfilled') setMarket(m.value.data)
            if (w.status === 'fulfilled') setTotals(w.value.data.totals)
            if (o.status === 'fulfilled') setLive(o.value.data.opportunities || [])
        }
        load()
        aiApi
            .voiceCommands()
            .then(({ data }) => setCommands(data.commands || []))
            .catch(() => setCommands([]))
        const timer = setInterval(load, 60000)
        return () => {
            alive = false
            clearInterval(timer)
        }
    }, [])

    useEffect(() => {
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    }, [messages, voice.interim])

    // --- Text path (streamed) --------------------------------------------

    const send = useCallback(
        async (text) => {
            const question = (text ?? input).trim()
            if (!question || busy) return

            setInput('')
            push({ role: 'user', text: question, via: 'text' })
            setBusy(true)

            // Typed text gets the same commands as voice ("paper trade the
            // picks", "today's pick"…). Rule matching is instant; only an
            // unmatched question goes on to the model.
            try {
                const { data } = await aiApi.voiceCommand(question, false)
                if (data.source === 'command') {
                    push({ role: 'assistant', text: data.response || data.speech, action: data.action,
                           link: data.navigate, via: 'text' })
                    if (autoSpeak && data.speech) voice.speak(data.speech)
                    setBusy(false)
                    return
                }
            } catch {
                // Command routing unavailable: fall through to the model.
            }

            const controller = new AbortController()
            streamAbort.current = controller
            let assembled = ''
            let placed = false

            try {
                await aiApi.stream(question, {
                    signal: controller.signal,
                    onEvent: (event) => {
                        if (event.type === 'meta') {
                            push({
                                role: 'assistant',
                                text: '',
                                streaming: true,
                                symbols: event.symbols,
                                intent: event.intent,
                            })
                            placed = true
                        } else if (event.type === 'token') {
                            assembled += event.text
                            setMessages((prev) => {
                                const next = [...prev]
                                const last = next[next.length - 1]
                                if (last?.streaming) last.text = assembled
                                return next
                            })
                        } else if (event.type === 'done') {
                            setMessages((prev) => {
                                const next = [...prev]
                                const last = next[next.length - 1]
                                if (last?.streaming) {
                                    last.text = event.text || assembled
                                    last.streaming = false
                                }
                                return next
                            })
                            if (autoSpeak && (event.text || assembled)) {
                                // Reshape server-side so speech gets the same
                                // treatment as the voice channel.
                                aiApi
                                    .prepareSpeech(event.text || assembled)
                                    .then(({ data }) => voice.speak(data.text))
                                    .catch(() => {})
                            }
                        }
                    },
                })
            } catch (err) {
                if (err.name !== 'AbortError') {
                    if (!placed) push({ role: 'assistant', text: '', streaming: false })
                    setMessages((prev) => {
                        const next = [...prev]
                        const last = next[next.length - 1]
                        if (last && last.role === 'assistant') {
                            last.text = `Connection to the backend failed. ${err.message}`
                            last.error = true
                            last.streaming = false
                        }
                        return next
                    })
                }
            } finally {
                setBusy(false)
                streamAbort.current = null
            }
        },
        [input, busy, push, autoSpeak, voice, navigate]
    )

    const orbState = voice.speaking
        ? 'speaking'
        : voice.thinking || busy
          ? 'thinking'
          : voice.listening
            ? 'listening'
            : brain && !brain.online
              ? 'offline'
              : 'idle'

    return (
        <div className="grid h-full grid-cols-1 gap-5 xl:grid-cols-[1fr_340px]">
            {/* ---- Conversation ---- */}
            <div className="flex min-h-0 flex-col gap-4">
                <div className="flex items-center justify-between gap-4">
                    <div className="flex items-center gap-4">
                        <CoreOrb
                            state={orbState}
                            amplitude={voice.amplitude}
                            size={92}
                            showLabel={false}
                            onClick={voice.toggle}
                        />
                        <div>
                            <h1 className="font-mono text-lg uppercase tracking-[0.3em] text-primary-300 text-glow">
                                Tradeo
                            </h1>
                            <p className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.2em] text-dark-400">
                                {voice.listening
                                    ? voice.ambient
                                        ? 'Ambient — say "Tradeo…"'
                                        : 'Listening'
                                    : voice.speaking
                                      ? 'Responding'
                                      : busy || voice.thinking
                                        ? 'Processing'
                                        : brain?.online
                                          ? `${brain.cloud?.available && brain.mode !== 'local_only' ? 'cloud + ' : 'local · '}${brain.local?.model?.split('/').pop() || 'local'}`
                                          : 'No reasoning engine'}
                            </p>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => setAutoSpeak((v) => !v)}
                            className="btn-ghost !px-2.5"
                            title={autoSpeak ? 'Mute replies' : 'Speak replies'}
                        >
                            {autoSpeak ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
                        </button>
                    </div>
                </div>

                <HudPanel
                    className="flex min-h-0 flex-1 flex-col"
                    padded={false}
                    title="Transcript"
                    right={
                        messages.length > 0 && (
                            <button
                                onClick={() => setMessages([])}
                                className="hud-label transition-colors hover:text-primary-300"
                            >
                                Clear
                            </button>
                        )
                    }
                >
                    <div ref={scrollRef} className="mask-fade-y min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
                        {messages.length === 0 && !voice.interim && (
                            <div className="flex flex-col items-center gap-5 py-10">
                                <CoreOrb state={orbState} amplitude={voice.amplitude} size={180} />
                                <Greeting brain={brain} automation={automation} />
                                <div className="flex flex-wrap justify-center gap-2">
                                    {SUGGESTIONS.map((s) => (
                                        <button
                                            key={s}
                                            onClick={() => send(s)}
                                            className="rounded-full border border-dark-600/60 px-3 py-1.5 text-xs text-dark-300 transition-all hover:border-primary-400/50 hover:text-primary-200"
                                        >
                                            {s}
                                        </button>
                                    ))}
                                </div>

                                {/*
                                  Manual fallback for every voice command.
                                  Same endpoint and same handler as speech, so
                                  nothing can work by voice and be broken here.
                                */}
                                {commands.length > 0 && (
                                    <div className="w-full max-w-lg">
                                        <p className="mb-2 text-center text-[11px] uppercase tracking-wider text-dark-500">
                                            Or run a command — no microphone needed
                                        </p>
                                        <div className="flex flex-wrap justify-center gap-1.5">
                                            {commands.map((c) => (
                                                <button
                                                    key={c.action}
                                                    onClick={() => runCommand(c.example)}
                                                    disabled={busy}
                                                    title={
                                                        c.needs_symbol
                                                            ? 'Edit the symbol in the box below, or say it'
                                                            : c.action
                                                    }
                                                    className="rounded-md border border-dark-700/70 bg-dark-800/40 px-2.5 py-1 text-[11px] text-dark-300 transition-all hover:border-cyan-400/50 hover:text-cyan-200 disabled:opacity-40"
                                                >
                                                    {c.example}
                                                    {c.needs_symbol && (
                                                        <span className="ml-1 text-dark-500">·</span>
                                                    )}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}

                        {messages.map((message, i) => (
                            <Message key={i} message={message} />
                        ))}

                        {voice.interim && (
                            <div className="flex justify-end">
                                <div className="max-w-[80%] rounded-lg border border-primary-400/25 bg-primary-500/5 px-4 py-2.5">
                                    <p className="text-sm italic text-primary-300/70">{voice.interim}</p>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* ---- Input ---- */}
                    <div className="border-t border-primary-400/10 p-3">
                        {voice.error && (
                            <p className="mb-2 flex items-center gap-2 text-xs text-danger-400">
                                <AlertTriangle className="h-3.5 w-3.5" /> {voice.error}
                            </p>
                        )}
                        <div className="flex items-center gap-2">
                            <button
                                onClick={voice.toggle}
                                disabled={!voice.supported}
                                className={voice.listening ? 'btn-alert !px-3' : 'btn-ghost !px-3'}
                                title={
                                    !voice.supported
                                        ? 'No microphone access in this browser'
                                        : voice.listening
                                          ? 'Click to send'
                                          : 'Click, speak, click again to send'
                                }
                            >
                                {voice.listening ? <Send className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                            </button>

                            <input
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
                                placeholder="Ask Tradeo…"
                                className="input-field flex-1"
                                disabled={busy}
                            />

                            {voice.speaking ? (
                                <button onClick={voice.stopSpeaking} className="btn-ghost !px-3" title="Stop speaking">
                                    <Square className="h-4 w-4" />
                                </button>
                            ) : (
                                <button
                                    onClick={() => send()}
                                    disabled={busy || !input.trim()}
                                    className="btn-primary !px-3"
                                >
                                    <Send className="h-4 w-4" />
                                </button>
                            )}
                        </div>
                    </div>
                </HudPanel>
            </div>

            {/* ---- Status rail ---- */}
            <aside className="hidden min-h-0 flex-col gap-4 overflow-y-auto xl:flex">
                <HudPanel title="Market" corners={false}>
                    {market ? (
                        <div className="space-y-3">
                            <div className="flex items-center justify-between">
                                <StatusDot
                                    status={market.is_open ? 'ok' : 'idle'}
                                    label={market.phase.replace('_', ' ')}
                                />
                                <span className="font-mono text-xs text-dark-400">{market.time_ist} IST</span>
                            </div>
                            <p className="font-mono text-[11px] text-dark-500">
                                {market.is_open
                                    ? `Closes in ${market.closes_in_minutes} min`
                                    : market.opens_in_minutes !== undefined
                                      ? `Opens in ${Math.floor(market.opens_in_minutes / 60)}h ${market.opens_in_minutes % 60}m`
                                      : market.session}
                            </p>
                        </div>
                    ) : (
                        <Loading rows={1} label="Market clock" />
                    )}
                </HudPanel>

                <PortfolioCard />

                <HudPanel
                    title="Live signals"
                    corners={false}
                    right={<Radio className="h-3.5 w-3.5 text-primary-400/60" />}
                >
                    {live.length > 0 ? (
                        <div className="space-y-2.5">
                            {live.map((o) => (
                                <button
                                    key={o.id}
                                    onClick={() => send(`Tell me about the ${o.symbol} signal`)}
                                    className="w-full rounded border border-dark-700/60 p-2.5 text-left transition-all hover:border-primary-400/40"
                                >
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="font-mono text-sm text-dark-100">{o.symbol}</span>
                                        <span
                                            className={
                                                o.verdict?.includes('buy')
                                                    ? 'badge-success'
                                                    : o.verdict === 'exit' || o.verdict === 'avoid'
                                                      ? 'badge-danger'
                                                      : 'badge-alert'
                                            }
                                        >
                                            {o.verdict?.replace('_', ' ')}
                                        </span>
                                    </div>
                                    <p className="mt-1 line-clamp-2 text-[11px] leading-snug text-dark-500">
                                        {o.thesis}
                                    </p>
                                </button>
                            ))}
                        </div>
                    ) : (
                        <Empty icon={Activity} title="Nothing flagged" hint="The scanner runs during market hours." />
                    )}
                </HudPanel>

                <ResearchRail />
            </aside>
        </div>
    )
}

/**
 * The first thing on screen: who's speaking, what's running, how to start.
 * Nothing runs in the background until it is switched on in Autopilot, and
 * the greeting says so rather than letting the app look busy or broken.
 */
function Greeting({ brain, automation }) {
    const hour = new Date().getHours()
    const part = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
    const title = brain?.operator_title ? `, ${brain.operator_title}` : ''
    const on = (automation || []).filter((a) => a.enabled).map((a) => a.name.replace(/ \(.*\)/, ''))
    const model = brain?.local?.model?.split('/').pop()?.split(':')[0]

    return (
        <div className="max-w-md space-y-2 text-center">
            <p className="text-lg text-dark-100">{part}{title}. I'm {brain?.assistant || 'Tradeo'}.</p>
            <p className="text-sm leading-relaxed text-dark-400">
                {brain?.online
                    ? `Running on this laptop${model ? ` (${model})` : ''} — nothing leaves this machine.`
                    : 'My local AI isn\'t running yet — start Ollama and I\'ll be ready.'}{' '}
                {automation === null
                    ? ''
                    : on.length
                      ? `Running in the background: ${on.join(', ')}.`
                      : 'Nothing is running in the background — Watchtower, the Fly brain and the Daily pick are off until you switch them on in Autopilot.'}
            </p>
            <p className="text-sm text-dark-300">
                Click the mic <Mic className="inline h-3.5 w-3.5" /> and speak, or type below. Ask about any NSE stock, ETF, REIT, InvIT or bond.
            </p>
        </div>
    )
}

function Message({ message }) {
    const isUser = message.role === 'user'

    if (isUser) {
        return (
            <div className="flex animate-slide-up justify-end">
                <div className="max-w-[82%] rounded-lg border border-primary-400/25 bg-primary-500/10 px-4 py-2.5">
                    <p className="text-sm text-dark-50">{message.text}</p>
                    {message.via === 'voice' && (
                        <span className="mt-1 flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest text-primary-400/60">
                            <Mic className="h-2.5 w-2.5" /> voice
                        </span>
                    )}
                </div>
            </div>
        )
    }

    return (
        <div className="flex animate-slide-up gap-3">
            <div className="mt-1 shrink-0">
                <Sparkles className="h-4 w-4 text-primary-400" />
            </div>
            <div className="min-w-0 flex-1">
                <div
                    className={`prose-hud text-sm leading-relaxed ${message.error ? 'text-danger-300' : 'text-dark-200'}`}
                >
                    <Markdown text={message.text} />
                    {message.streaming && (
                        <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-primary-400 align-middle" />
                    )}
                </div>
                {message.symbols?.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                        {message.symbols.map((s) => (
                            <a key={s} href={`#/stock/${s}`} className="badge-primary">
                                {s}
                            </a>
                        ))}
                    </div>
                )}
                {message.link && (
                    <a href={`#${message.link}`} className="mt-2 inline-block text-xs text-primary-300 hover:underline">
                        Open {message.link.replace('/', '').replace('-', ' ') || 'page'} →
                    </a>
                )}
                {message.meta?.provider && (
                    <p className="mt-1.5 font-mono text-[9px] uppercase tracking-widest text-dark-600">
                        {message.meta.provider} · {message.meta.latency_ms}ms
                    </p>
                )}
            </div>
        </div>
    )
}

/**
 * Minimal markdown renderer.
 *
 * The model emits bold, bullets and headers — enough that raw text looks
 * broken, but not enough to justify pulling in a parser.
 */
function Markdown({ text }) {
    if (!text) return null

    return text.split('\n').map((line, i) => {
        const trimmed = line.trim()
        if (!trimmed) return <div key={i} className="h-2" />

        const bullet = /^[-*•]\s+/.test(trimmed)
        const heading = /^#{1,6}\s+/.test(trimmed)
        const content = trimmed.replace(/^[-*•]\s+/, '').replace(/^#{1,6}\s+/, '')

        const rendered = content.split(/(\*\*[^*]+\*\*)/g).map((part, j) =>
            part.startsWith('**') && part.endsWith('**') ? (
                <strong key={j} className="font-semibold text-dark-50">
                    {part.slice(2, -2)}
                </strong>
            ) : (
                <span key={j}>{part}</span>
            )
        )

        if (heading) {
            return (
                <p key={i} className="mb-1 mt-3 font-mono text-xs uppercase tracking-[0.15em] text-primary-300">
                    {rendered}
                </p>
            )
        }
        if (bullet) {
            return (
                <div key={i} className="flex gap-2 py-0.5">
                    <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-primary-400/70" />
                    <span>{rendered}</span>
                </div>
            )
        }
        return (
            <p key={i} className="py-0.5">
                {rendered}
            </p>
        )
    })
}

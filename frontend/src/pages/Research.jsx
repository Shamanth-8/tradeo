import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
    Bot, Check, ChevronDown, ChevronRight, Copy, Loader2, Paperclip, Pencil,
    Plus, Search, Send, Sparkles, Square, Trash2, User, Wrench, X,
} from 'lucide-react'
import ResearchNav, { LocalModelNote, useEngine } from '../components/research/ResearchNav'
import Markdown from '../components/research/Markdown'
import { ErrorNote } from '../components/hud/HudPanel'
import { researchApi } from '../services/api'

/**
 * Research Agent: the research engine's agent inside Tradeo.
 *
 * It plans, writes and backtests strategies, and uses Tradeo's tools for
 * Indian market data, analysis, news and the paper book. It never places a
 * trade itself; paper picks go through Tradeo's gates.
 *
 * Replies stream over SSE. The stored message list is the source of truth, so
 * the live buffer is dropped and the list reloaded when an attempt ends.
 */

const STARTERS = [
    { tag: 'Tradeo', text: "What is today's Tradeo pick, and how are the open paper positions doing?" },
    { tag: 'Tradeo', text: 'Summarise the Indian market mood, the latest news and upcoming earnings this week.' },
    { tag: 'Backtest', text: 'Backtest a 20/50 day SMA crossover on RELIANCE.NS over the last 3 years, with Indian costs.' },
    { tag: 'Backtest', text: 'Backtest buying NIFTYBEES.NS when RSI(14) < 30 and selling when it is > 60, since 2020.' },
    { tag: 'Compare', text: 'Compare 6-month momentum and volatility across TCS.NS, INFY.NS, HCLTECH.NS and WIPRO.NS.' },
    { tag: 'Analysis', text: 'Use Tradeo to analyse HDFCBANK, then tell me what would change your view.' },
]

const DONE_EVENTS = ['attempt.completed', 'attempt.failed', 'attempt.cancelled']
const TOOL_EVENTS = ['tool_call', 'tool_start', 'tool.started', 'tool_result', 'tool.completed']

const ATTACH_OPEN = '[Uploaded files: '
const ATTACH_CLOSE = ']\n\n'

// The engine's own envelope for uploaded files, so its agent finds them.
function withAttachments(text, files) {
    if (!files.length) return text
    return `${ATTACH_OPEN}${JSON.stringify(files.map((f) => ({ filename: f.filename, path: f.file_path })))}${ATTACH_CLOSE}${text}`
}

function splitAttachments(content = '') {
    if (!content.startsWith(ATTACH_OPEN)) return { text: content, files: [] }
    const end = content.indexOf(ATTACH_CLOSE)
    try {
        const files = JSON.parse(content.slice(ATTACH_OPEN.length, end)).map((f) => f.filename)
        return { text: content.slice(end + ATTACH_CLOSE.length), files }
    } catch {
        return { text: content, files: [] }
    }
}

const toolLabel = (name) => String(name || 'tool').replace(/^mcp_tradeo_/, '').replace(/^tradeo_/, 'tradeo · ')

export default function Research() {
    const [engine, reloadEngine] = useEngine()
    const [params, setParams] = useSearchParams()
    const [sessions, setSessions] = useState([])
    const [filter, setFilter] = useState('')
    const [active, setActive] = useState(null)
    const [messages, setMessages] = useState([])
    const [live, setLive] = useState({ text: '', tools: [], running: false, started: 0 })
    const [input, setInput] = useState('')
    const [files, setFiles] = useState([])
    const [uploading, setUploading] = useState(false)
    const [skills, setSkills] = useState([])
    const [showSkills, setShowSkills] = useState(false)
    const [error, setError] = useState(null)
    const [now, setNow] = useState(Date.now())
    const endRef = useRef(null)
    const fileRef = useRef(null)
    const inputRef = useRef(null)

    const fail = (err) => setError(err?.response?.data?.detail || err?.message || String(err))

    const loadSessions = useCallback(async () => {
        try {
            const res = await researchApi.sessions()
            setSessions(res.data || [])
        } catch (err) {
            fail(err)
        }
    }, [])

    const loadMessages = useCallback(async (id) => {
        try {
            setMessages((await researchApi.messages(id)).data || [])
        } catch (err) {
            fail(err)
        }
    }, [])

    useEffect(() => {
        if (!engine?.online) return
        loadSessions()
        researchApi.skills().then((r) => setSkills(Array.isArray(r.data) ? r.data : [])).catch(() => {})
    }, [engine?.online, loadSessions])

    // A prompt handed over from elsewhere in Tradeo (?q=…) is sent once.
    const handedOver = useRef(false)
    useEffect(() => {
        const q = params.get('q')
        if (q && engine?.online && !handedOver.current) {
            handedOver.current = true
            setParams({}, { replace: true })
            send(q, true)
        }
    }, [engine?.online]) // eslint-disable-line react-hooks/exhaustive-deps

    // A ticking clock for the elapsed timer while the agent works.
    useEffect(() => {
        if (!live.running) return undefined
        const timer = setInterval(() => setNow(Date.now()), 1000)
        return () => clearInterval(timer)
    }, [live.running])

    // One event stream per open session.
    useEffect(() => {
        if (!active) return undefined
        setLive({ text: '', tools: [], running: false, started: 0 })
        loadMessages(active)

        const source = researchApi.events(active)
        const on = (name, handler) =>
            source.addEventListener(name, (e) => {
                try {
                    handler(JSON.parse(e.data || '{}'))
                } catch {
                    /* a malformed frame must not kill the stream */
                }
            })

        on('attempt.started', () => setLive({ text: '', tools: [], running: true, started: Date.now() }))
        on('text_delta', (d) => setLive((s) => ({ ...s, running: true, text: s.text + (d.delta || '') })))
        for (const name of TOOL_EVENTS) {
            on(name, (d) =>
                setLive((s) => {
                    const tool = d.tool || d.name || d.tool_name
                    const done = name.includes('result') || name.includes('completed')
                    const tools = [...s.tools]
                    const open = done && tools.findLastIndex((t) => t.tool === tool && !t.done)
                    if (done && open >= 0) tools[open] = { ...tools[open], done: true, ok: (d.status || 'ok') === 'ok' }
                    else tools.push({ tool, done, ok: true })
                    return { ...s, running: true, tools }
                }))
        }
        for (const name of DONE_EVENTS) {
            on(name, (d) => {
                if (d.error) fail(d.error)
                setLive({ text: '', tools: [], running: false, started: 0 })
                loadMessages(active)
                loadSessions()
            })
        }
        return () => source.close()
    }, [active, loadMessages, loadSessions])

    useEffect(() => {
        endRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages, live.text, live.tools.length])

    const newSession = async () => {
        try {
            const res = await researchApi.createSession('New research')
            await loadSessions()
            setActive(res.data.session_id)
            setMessages([])
            inputRef.current?.focus()
        } catch (err) {
            fail(err)
        }
    }

    const removeSession = async (id) => {
        try {
            await researchApi.deleteSession(id)
            if (id === active) {
                setActive(null)
                setMessages([])
            }
            loadSessions()
        } catch (err) {
            fail(err)
        }
    }

    const renameSession = async (id, title) => {
        try {
            await researchApi.renameSession(id, title)
            loadSessions()
        } catch (err) {
            fail(err)
        }
    }

    const attach = async (list) => {
        setUploading(true)
        try {
            for (const file of list) {
                const res = await researchApi.upload(file)
                setFiles((f) => [...f, res.data])
            }
        } catch (err) {
            fail(err)
        } finally {
            setUploading(false)
        }
    }

    async function send(text = input, fresh = false) {
        const content = text.trim()
        if (!content || live.running) return
        setError(null)
        setInput('')
        const payload = withAttachments(content, files)
        setFiles([])
        try {
            let id = fresh ? null : active
            if (!id) {
                const res = await researchApi.createSession(content.slice(0, 60))
                id = res.data.session_id
                setActive(id)
                loadSessions()
            }
            setMessages((m) => [...m, { message_id: `local-${Date.now()}`, role: 'user', content: payload }])
            setLive({ text: '', tools: [], running: true, started: Date.now() })
            await researchApi.send(id, payload)
        } catch (err) {
            setLive((s) => ({ ...s, running: false }))
            fail(err)
        }
    }

    const stop = async () => {
        if (!active) return
        try {
            await researchApi.cancel(active)
        } catch (err) {
            fail(err)
        }
    }

    const shown = useMemo(() => {
        const q = filter.trim().toLowerCase()
        return q ? sessions.filter((s) => (s.title || '').toLowerCase().includes(q)) : sessions
    }, [sessions, filter])

    const elapsed = live.started ? Math.max(0, Math.round((now - live.started) / 1000)) : 0
    const offline = !engine?.online

    return (
        <div className="flex h-[calc(100vh-7rem)] flex-col gap-3 animate-fade-in">
            <ResearchNav
                title="Research Agent"
                subtitle="Plans, codes and backtests strategies, using Tradeo for Indian prices, analysis, news and the paper book."
                engine={engine}
                onEngineChange={reloadEngine}
            />
            <LocalModelNote engine={engine} />
            <ErrorNote error={error} onRetry={() => setError(null)} />

            <div className="flex min-h-0 flex-1 gap-3">
                {/* Sessions */}
                <aside className="hud-panel flex w-64 shrink-0 flex-col">
                    <div className="flex items-center gap-2 border-b border-primary-400/10 p-2">
                        <div className="relative flex-1">
                            <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-dark-500" />
                            <input
                                value={filter}
                                onChange={(e) => setFilter(e.target.value)}
                                placeholder="Find a session"
                                className="w-full rounded border border-dark-800 bg-dark-950/60 py-1 pl-6 pr-2 text-xs text-dark-100 outline-none focus:border-primary-400/40"
                            />
                        </div>
                        <button onClick={newSession} disabled={offline} className="btn-primary !px-2 !py-1" title="New session">
                            <Plus className="h-3.5 w-3.5" />
                        </button>
                    </div>
                    <div className="flex-1 space-y-0.5 overflow-y-auto p-2">
                        {shown.length === 0 && <p className="px-2 py-4 text-xs text-dark-500">No sessions yet.</p>}
                        {shown.map((s) => (
                            <SessionRow
                                key={s.session_id}
                                session={s}
                                active={s.session_id === active}
                                onOpen={() => setActive(s.session_id)}
                                onDelete={() => removeSession(s.session_id)}
                                onRename={(t) => renameSession(s.session_id, t)}
                            />
                        ))}
                    </div>
                    <button
                        onClick={() => setShowSkills((v) => !v)}
                        className="flex items-center justify-between border-t border-primary-400/10 px-3 py-2 text-xs text-dark-400 hover:text-primary-300"
                    >
                        <span className="flex items-center gap-2">
                            <Sparkles className="h-3.5 w-3.5" /> {skills.length} skills
                        </span>
                        {showSkills ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    </button>
                </aside>

                {/* Conversation */}
                <section
                    className="hud-panel relative flex min-w-0 flex-1 flex-col"
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                        e.preventDefault()
                        if (e.dataTransfer.files?.length) attach([...e.dataTransfer.files])
                    }}
                >
                    <div className="flex-1 space-y-5 overflow-y-auto p-5">
                        {messages.length === 0 && !live.running ? (
                            <Welcome disabled={offline} onPick={(t) => send(t)} />
                        ) : (
                            messages.map((m) => <Message key={m.message_id} message={m} />)
                        )}

                        {live.running && (
                            <div className="flex gap-3 animate-slide-up">
                                <Avatar role="assistant" busy />
                                <div className="min-w-0 max-w-[85%] flex-1 space-y-2">
                                    <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-dark-500">
                                        <Loader2 className="h-3 w-3 animate-spin text-primary-400" />
                                        Working · {elapsed}s
                                        {live.tools.length > 0 && ` · ${live.tools.length} tool call${live.tools.length > 1 ? 's' : ''}`}
                                    </div>
                                    {live.tools.length > 0 && (
                                        <div className="flex flex-wrap gap-1">
                                            {live.tools.map((t, i) => <ToolChip key={i} {...t} />)}
                                        </div>
                                    )}
                                    {live.text && (
                                        <div className="rounded-lg rounded-tl-none border border-dark-800 bg-dark-900/70 px-4 py-3 text-dark-100">
                                            <Markdown>{live.text}</Markdown>
                                            <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-primary-400 align-middle" />
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                        <div ref={endRef} />
                    </div>

                    {/* Composer */}
                    <div className="border-t border-primary-400/10 p-3">
                        {files.length > 0 && (
                            <div className="mb-2 flex flex-wrap gap-1">
                                {files.map((f, i) => (
                                    <span key={i} className="badge-primary flex items-center gap-1">
                                        <Paperclip className="h-3 w-3" />
                                        {f.filename}
                                        <button onClick={() => setFiles((all) => all.filter((_, j) => j !== i))}>
                                            <X className="h-3 w-3" />
                                        </button>
                                    </span>
                                ))}
                            </div>
                        )}
                        <div className="flex items-end gap-2">
                            <input
                                ref={fileRef}
                                type="file"
                                multiple
                                className="hidden"
                                onChange={(e) => {
                                    attach([...e.target.files])
                                    e.target.value = ''
                                }}
                            />
                            <button
                                onClick={() => fileRef.current?.click()}
                                disabled={offline || uploading}
                                className="press rounded-md border border-dark-700 p-2.5 text-dark-400 hover:border-primary-400/40 hover:text-primary-300 disabled:opacity-40"
                                title="Attach CSV, Excel, PDF or text (or drop files here)"
                            >
                                {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
                            </button>
                            <textarea
                                ref={inputRef}
                                rows={Math.min(6, Math.max(1, input.split('\n').length))}
                                className="input-field flex-1 resize-none"
                                placeholder={offline ? 'Research engine is offline' : 'Ask for research or a backtest…  (Enter to send, Shift+Enter for a new line)'}
                                value={input}
                                disabled={offline}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && !e.shiftKey) {
                                        e.preventDefault()
                                        send()
                                    }
                                }}
                            />
                            {live.running ? (
                                <button onClick={stop} className="btn-danger px-4 py-2.5" title="Stop">
                                    <Square className="h-4 w-4" />
                                </button>
                            ) : (
                                <button onClick={() => send()} disabled={offline || !input.trim()} className="btn-primary px-4 py-2.5 disabled:opacity-50">
                                    <Send className="h-4 w-4" />
                                </button>
                            )}
                        </div>
                    </div>
                </section>

                {showSkills && (
                    <SkillsPanel
                        skills={skills}
                        onClose={() => setShowSkills(false)}
                        onUse={(s) => {
                            setInput((v) => `Use the ${s.name} skill: ${v}`)
                            inputRef.current?.focus()
                        }}
                    />
                )}
            </div>
        </div>
    )
}

function Welcome({ onPick, disabled }) {
    return (
        <div className="mx-auto flex max-w-3xl flex-col items-center gap-6 py-8 text-center">
            <div className="relative">
                <div className="absolute inset-0 animate-ping rounded-full bg-primary-500/10" />
                <div className="relative flex h-14 w-14 items-center justify-center rounded-full border border-primary-400/30 bg-primary-500/10 shadow-glow">
                    <Bot className="h-6 w-6 text-primary-300" />
                </div>
            </div>
            <div>
                <h2 className="text-lg font-semibold text-dark-50">What should we research?</h2>
                <p className="mt-1 text-sm text-dark-400">
                    Use NSE tickers like <code className="text-primary-300">RELIANCE.NS</code>. Backtests land in Runs; attach a CSV to analyse your own data.
                </p>
            </div>
            <div className="grid w-full grid-cols-1 gap-2 md:grid-cols-2">
                {STARTERS.map((s, i) => (
                    <button
                        key={s.text}
                        onClick={() => onPick(s.text)}
                        disabled={disabled}
                        style={{ animationDelay: `${i * 60}ms` }}
                        className="press group animate-slide-up rounded-lg border border-dark-800 bg-dark-900/40 px-4 py-3 text-left transition-all hover:-translate-y-0.5 hover:border-primary-400/40 hover:bg-primary-500/5 disabled:opacity-40"
                    >
                        <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-primary-400/70">{s.tag}</span>
                        <p className="mt-1 text-xs text-dark-300 group-hover:text-dark-100">{s.text}</p>
                    </button>
                ))}
            </div>
        </div>
    )
}

function SessionRow({ session, active, onOpen, onDelete, onRename }) {
    const [editing, setEditing] = useState(false)
    const [title, setTitle] = useState('')
    const clean = (session.title || 'Untitled').replace(/^#+\s*/, '')

    if (editing) {
        return (
            <form
                onSubmit={(e) => {
                    e.preventDefault()
                    setEditing(false)
                    if (title.trim()) onRename(title.trim())
                }}
                className="flex items-center gap-1 px-1"
            >
                <input
                    autoFocus
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    onBlur={() => setEditing(false)}
                    className="w-full rounded border border-primary-400/40 bg-dark-950 px-2 py-1 text-xs text-dark-100 outline-none"
                />
            </form>
        )
    }
    return (
        <div
            className={`group flex items-center gap-1 rounded px-2 py-1.5 text-xs transition-colors ${
                active ? 'bg-primary-500/10 text-primary-200' : 'text-dark-400 hover:bg-dark-800/50 hover:text-dark-100'
            }`}
        >
            <button onClick={onOpen} className="min-w-0 flex-1 truncate text-left" title={clean}>
                {clean}
            </button>
            <button
                onClick={() => {
                    setTitle(clean)
                    setEditing(true)
                }}
                className="hidden text-dark-500 hover:text-primary-300 group-hover:block"
                title="Rename"
            >
                <Pencil className="h-3 w-3" />
            </button>
            <button onClick={onDelete} className="hidden text-dark-500 hover:text-danger-400 group-hover:block" title="Delete">
                <Trash2 className="h-3 w-3" />
            </button>
        </div>
    )
}

function SkillsPanel({ skills, onUse, onClose }) {
    const [q, setQ] = useState('')
    const shown = skills.filter((s) => `${s.name} ${s.description}`.toLowerCase().includes(q.toLowerCase()))
    return (
        <aside className="hud-panel flex w-72 shrink-0 flex-col animate-slide-up">
            <div className="flex items-center gap-2 border-b border-primary-400/10 p-2">
                <input
                    autoFocus
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    placeholder="Search skills"
                    className="flex-1 rounded border border-dark-800 bg-dark-950/60 px-2 py-1 text-xs text-dark-100 outline-none focus:border-primary-400/40"
                />
                <button onClick={onClose} className="text-dark-500 hover:text-dark-100">
                    <X className="h-4 w-4" />
                </button>
            </div>
            <div className="flex-1 space-y-1 overflow-y-auto p-2">
                {shown.map((s) => (
                    <button
                        key={s.name}
                        onClick={() => onUse(s)}
                        className="w-full rounded border border-transparent px-2 py-1.5 text-left transition-colors hover:border-primary-400/30 hover:bg-primary-500/5"
                    >
                        <div className="font-mono text-[11px] text-primary-300">{s.name}</div>
                        <div className="line-clamp-2 text-[11px] text-dark-500">{s.description}</div>
                    </button>
                ))}
            </div>
        </aside>
    )
}

function Avatar({ role, busy }) {
    const user = role === 'user'
    return (
        <div
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
                user ? 'bg-dark-700 text-dark-200' : `bg-primary-500/15 text-primary-300 ${busy ? 'shadow-glow' : ''}`
            }`}
        >
            {user ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
        </div>
    )
}

function ToolChip({ tool, done, ok = true }) {
    return (
        <span
            className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 font-mono text-[10px] transition-colors ${
                !done
                    ? 'border-primary-400/40 bg-primary-500/10 text-primary-200'
                    : ok
                        ? 'border-success-400/20 bg-success-500/5 text-success-300'
                        : 'border-danger-400/30 bg-danger-500/10 text-danger-300'
            }`}
        >
            {!done ? <Loader2 className="h-3 w-3 animate-spin" /> : ok ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
            {toolLabel(tool)}
        </span>
    )
}

function Message({ message }) {
    const user = message.role === 'user'
    const { text, files } = splitAttachments(message.content)
    const tools = message.tool_trail || []
    const [open, setOpen] = useState(false)
    const [copied, setCopied] = useState(false)

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(text)
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
        } catch {
            /* clipboard blocked */
        }
    }

    return (
        <div className={`group flex gap-3 animate-fade-in ${user ? 'justify-end' : ''}`}>
            {!user && <Avatar role="assistant" />}
            <div className={`min-w-0 max-w-[85%] space-y-1.5 ${user ? 'items-end' : ''}`}>
                {tools.length > 0 && (
                    <button
                        onClick={() => setOpen((v) => !v)}
                        className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-dark-500 hover:text-primary-300"
                    >
                        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                        <Wrench className="h-3 w-3" /> {tools.length} tool call{tools.length > 1 ? 's' : ''}
                    </button>
                )}
                {open && (
                    <div className="flex flex-wrap gap-1">
                        {tools.map((t, i) => (
                            <ToolChip key={i} tool={t.tool || t.name} done ok={(t.status || 'ok') === 'ok'} />
                        ))}
                    </div>
                )}
                {files.length > 0 && (
                    <div className="flex flex-wrap justify-end gap-1">
                        {files.map((f) => (
                            <span key={f} className="badge-muted flex items-center gap-1">
                                <Paperclip className="h-3 w-3" /> {f}
                            </span>
                        ))}
                    </div>
                )}
                <div
                    className={`relative rounded-lg px-4 py-3 ${
                        user
                            ? 'rounded-tr-none bg-primary-600/80 text-white'
                            : 'rounded-tl-none border border-dark-800 bg-dark-900/70 text-dark-100'
                    }`}
                >
                    <Markdown>{text}</Markdown>
                    {!user && (
                        <button
                            onClick={copy}
                            className="absolute right-2 top-2 hidden rounded p-1 text-dark-500 hover:bg-dark-800 hover:text-dark-100 group-hover:block"
                            title="Copy"
                        >
                            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                        </button>
                    )}
                </div>
            </div>
            {user && <Avatar role="user" />}
        </div>
    )
}

import { useCallback, useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import {
    AlertTriangle, Bot, Check, Cpu, Database, Key, Mic, Plug, RefreshCw, Save, X, Volume2,
} from 'lucide-react'
import { HudPanel, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import ConnectionStatus from '../components/ConnectionStatus'
import BrokerConnections from '../components/BrokerConnections'
import { aiApi, setupApi } from '../services/api'
import useVoice from '../hooks/useVoice'
import { startLocalListener } from '../hooks/localSpeech'

/**
 * Setup — connect everything without editing .env.
 *
 * Secrets are write-only here: the API returns a masked hint (…abcd) and never
 * the value, so nothing sensitive is ever held in browser state.
 */

const GROUPS = {
    ai: {
        label: 'Intelligence',
        icon: Cpu,
        hint: 'Tradeo runs fully local on Ollama by default (AI_MODE=local_only). An OpenRouter key is optional: it powers the research agent, and the rest of the app only if you change AI_MODE.',
    },
    broker: {
        label: 'Broker',
        icon: Plug,
        hint: "Optional — paper trading needs no broker. Zerodha, Dhan, Angel One and Kotak Neo are built in; others can be added as plugins (backend/brokers/plugins). Zerodha needs a daily login: /api/setup/brokers/zerodha/login. Live orders stay off until you enable them per broker.",
    },
    data: {
        label: 'Market data',
        icon: Database,
        hint: 'Finnhub (free) adds IPO and earnings calendars. SEBI, NSE and RBI feeds need no key.',
    },
    telegram: {
        label: 'Telegram',
        icon: Bot,
        hint: 'Create a bot with @BotFather, then send it any message — a bot cannot start the conversation itself.',
    },
}

export default function Setup() {
    const [config, setConfig] = useState(null)
    const [voice, setVoice] = useState(null)
    const [drafts, setDrafts] = useState({})
    const [saving, setSaving] = useState(false)
    const [tests, setTests] = useState({})
    const [error, setError] = useState(null)
    const [loading, setLoading] = useState(true)
    const focusBrokers = new URLSearchParams(useLocation().search).get('section') === 'brokers'

    const load = useCallback(async () => {
        try {
            const [c, v] = await Promise.all([
                setupApi.config(),
                setupApi.voice(),
            ])
            setConfig(c.data)
            setVoice(v.data)
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        load()
    }, [load])

    const save = async () => {
        if (Object.keys(drafts).length === 0) return
        setSaving(true)
        setError(null)
        try {
            await setupApi.saveConfig(drafts)
            setDrafts({})
            await load()
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        } finally {
            setSaving(false)
        }
    }

    const runTest = async (name, fn) => {
        setTests((prev) => ({ ...prev, [name]: { running: true } }))
        try {
            const { data } = await fn()
            setTests((prev) => ({ ...prev, [name]: { running: false, result: data } }))
        } catch (err) {
            setTests((prev) => ({
                ...prev,
                [name]: { running: false, result: { error: err?.response?.data?.detail || err.message } },
            }))
        }
        load()
    }

    if (loading) return <Loading rows={5} label="Reading configuration" />

    const dirty = Object.keys(drafts).length > 0

    return (
        <div className="space-y-5">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="font-mono text-lg uppercase tracking-[0.3em] text-primary-300 text-glow">
                        Connections
                    </h1>
                    <p className="mt-1 text-xs text-dark-400">
                        Credentials apply immediately — no restart. Stored locally, never sent anywhere but the service they belong to.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button onClick={load} className="btn-ghost">
                        <RefreshCw className="h-3.5 w-3.5" /> Reload
                    </button>
                    <button onClick={save} disabled={!dirty || saving} className="btn-primary">
                        <Save className="h-3.5 w-3.5" />
                        {saving ? 'Saving…' : dirty ? `Save ${Object.keys(drafts).length}` : 'Saved'}
                    </button>
                </div>
            </header>

            <ErrorNote error={error} onRetry={load} />

            {/*
              Live connection state, above the static checklist.

              The checklist below answers "is a key present". This answers "does
              it work, and if not, what do I do" — which is the question people
              actually have when something says not connected.
            */}
            <ConnectionStatus />

            {/*
              The old "System readiness" grid lived here. It was removed rather
              than restyled: it only checked whether a credential *string
              existed*, so it rendered "6/6 subsystems online" with the cloud model and
              Telegram green while ConnectionStatus above correctly reported
              both as blocked. Two panels disagreeing about the same fact is
              worse than one — and the reassuring one is the one people believe.
            */}

            {/* ---- Brokers: one card each ---- */}
            <BrokerConnections
                focus={focusBrokers}
                fields={config?.groups?.broker || []}
                renderField={(field) => (
                    <Field
                        key={field.key}
                        field={field}
                        draft={drafts[field.key]}
                        onChange={(value) => setDrafts((prev) => ({ ...prev, [field.key]: value }))}
                        onClear={() =>
                            setDrafts((prev) => {
                                const next = { ...prev }
                                delete next[field.key]
                                return next
                            })
                        }
                    />
                )}
                renderTest={(b) => (
                    <div className="space-y-2 border-t border-dark-800/60 pt-3">
                        <TestButton
                            label={`Test ${b.display_name}`}
                            state={tests[`broker:${b.broker}`]}
                            onClick={() => runTest(`broker:${b.broker}`, () => setupApi.testBroker(b.broker))}
                        />
                        {tests[`broker:${b.broker}`]?.result && (
                            <TestResult name={b.broker} result={tests[`broker:${b.broker}`].result} />
                        )}
                    </div>
                )}
            />
            {dirty && (
                <p className="text-[11px] text-alert-300">Unsaved changes — press Save at the top.</p>
            )}

            {/* ---- Other credentials ---- */}
            {Object.entries(config?.groups || {}).filter(([groupId]) => groupId !== 'broker').map(([groupId, fields]) => {
                const group = GROUPS[groupId] || { label: groupId, icon: Key, hint: '' }
                const Icon = group.icon
                return (
                    <HudPanel
                        key={groupId}
                        title={group.label}
                        subtitle={group.hint}
                        right={<Icon className="h-4 w-4 text-primary-400/60" />}
                    >
                        <div className="space-y-3">
                            {fields.map((field) => (
                                <Field
                                    key={field.key}
                                    field={field}
                                    draft={drafts[field.key]}
                                    onChange={(value) =>
                                        setDrafts((prev) => ({ ...prev, [field.key]: value }))
                                    }
                                    onClear={() =>
                                        setDrafts((prev) => {
                                            const next = { ...prev }
                                            delete next[field.key]
                                            return next
                                        })
                                    }
                                />
                            ))}

                            <div className="flex flex-wrap gap-2 border-t border-dark-800/60 pt-3">
                                {groupId === 'ai' && (
                                    <>
                                        <TestButton
                                            label="Test reasoning"
                                            state={tests.ai}
                                            onClick={() => runTest('ai', setupApi.testAi)}
                                        />
                                    </>
                                )}
                                {groupId === 'telegram' && (
                                    <TestButton
                                        label="Test Telegram"
                                        state={tests.telegram}
                                        onClick={() => runTest('telegram', setupApi.testTelegram)}
                                    />
                                )}
                            </div>

                            {Object.entries(tests)
                                .filter(([name]) =>
                                    groupId === 'ai' ? name === 'ai' : name === groupId
                                )
                                .map(([name, state]) =>
                                    state.result ? (
                                        <TestResult key={name} name={name} result={state.result} />
                                    ) : null
                                )}
                        </div>
                    </HudPanel>
                )
            })}

            {/* ---- Voice ---- */}
            <HudPanel
                title="Voice"
                subtitle="Runs entirely on this machine — no speech data leaves it"
                right={<Mic className="h-4 w-4 text-primary-400/60" />}
            >
                <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1.5">
                        <div className="hud-label">Recognition</div>
                        <p className="font-mono text-sm text-dark-100">{voice?.stt?.active}</p>
                        <p className="text-[11px] leading-snug text-dark-500">
                            {voice?.stt?.local_whisper?.installed
                                ? `Local Whisper available (${voice.stt.local_whisper.model})`
                                : 'Browser Web Speech API. No install, nothing uploaded.'}
                        </p>
                    </div>
                    <div className="space-y-1.5">
                        <div className="hud-label">Speech</div>
                        <p className="font-mono text-sm text-dark-100">{voice?.tts?.active}</p>
                        <p className="text-[11px] leading-snug text-dark-500">
                            Wake word: <span className="text-primary-300">"{voice?.wake_word}"</span>
                        </p>
                    </div>
                </div>
                <VoiceTest />

                {voice?.install_hint && (
                    <p className="mt-3 rounded border border-dark-700/60 px-3 py-2 font-mono text-[11px] text-dark-400">
                        {voice.install_hint}
                    </p>
                )}
            </HudPanel>
        </div>
    )
}

/**
 * Speech check.
 *
 * Isolates text-to-speech from the rest of the voice pipeline: if this is
 * silent the problem is the browser or the OS audio, not Tradeo. It reports
 * the voice actually selected, which is usually what explains a bad accent.
 */
function VoiceTest() {
    const [state, setState] = useState(null)
    const [heard, setHeard] = useState(null)
    const [recording, setRecording] = useState(false)
    const { speak, speaking, error, engine, speechSupported } = useVoice({ autoSpeak: false })

    const test = () => {
        if (engine.tts === 'piper') {
            setState({ chosen: 'Piper (local neural voice)' })
        } else {
            const voices = window.speechSynthesis?.getVoices() || []
            const chosen =
                voices.find((v) => /en[-_]IN/i.test(v.lang)) ||
                voices.find((v) => /en[-_]GB/i.test(v.lang)) ||
                voices.find((v) => v.lang?.startsWith('en'))
            setState({
                supported: speechSupported,
                chosen: `${voices.length} browser voices · ${chosen ? `${chosen.name} (${chosen.lang})` : 'system default'}`,
            })
        }
        speak('Tradeo online. Nifty tracking active, watchtower armed. All systems nominal.')
    }

    // One utterance through the local listener and Whisper, shown as text.
    const testMic = async () => {
        setHeard(null)
        setRecording(true)
        let listener
        const timeout = setTimeout(() => {
            listener?.stop()
            setRecording(false)
            setHeard({ error: 'Heard nothing in 8 seconds. Check the microphone input level.' })
        }, 8000)
        try {
            listener = await startLocalListener({
                onUtterance: async (wav) => {
                    clearTimeout(timeout)
                    listener.stop()
                    setRecording(false)
                    try {
                        const started = performance.now()
                        const { data } = await aiApi.transcribe(wav)
                        setHeard({ text: data.text || '(silence)', ms: Math.round(performance.now() - started) })
                    } catch (err) {
                        setHeard({ error: err?.response?.data?.detail || err.message })
                    }
                },
            })
        } catch (err) {
            clearTimeout(timeout)
            setRecording(false)
            setHeard({ error: err?.name === 'NotAllowedError' ? 'Microphone permission denied' : err.message })
        }
    }

    return (
        <div className="mt-4 border-t border-dark-800/60 pt-3">
            <div className="flex flex-wrap items-center gap-2">
                <button onClick={test} disabled={speaking} className="btn-ghost">
                    <Volume2 className="h-3.5 w-3.5" />
                    {speaking ? 'Speaking…' : 'Test speech'}
                </button>
                <button onClick={testMic} disabled={recording} className="btn-ghost">
                    <Mic className="h-3.5 w-3.5" />
                    {recording ? 'Listening… say something' : 'Test microphone'}
                </button>
                <span className="font-mono text-[10px] text-dark-500">
                    in: {engine.stt === 'local' ? 'Whisper (local)' : 'browser'} · out: {engine.tts === 'piper' ? 'Piper (local)' : 'browser'}
                </span>
            </div>
            {state && <p className="mt-2 font-mono text-[10px] text-dark-500">{state.chosen}</p>}
            {heard && (
                <p className={`mt-2 text-xs ${heard.error ? 'text-danger-400' : 'text-success-300'}`}>
                    {heard.error ? heard.error : <>Heard: “{heard.text}” <span className="text-dark-500">({heard.ms} ms)</span></>}
                </p>
            )}
            {error && (
                <p className="mt-2 flex items-center gap-1.5 text-[11px] text-danger-400">
                    <AlertTriangle className="h-3 w-3" /> {error}
                </p>
            )}
            {state && !error && !speaking && (
                <p className="mt-2 text-[11px] text-dark-500">
                    Heard nothing? Check system volume and that the browser tab isn't muted.
                </p>
            )}
        </div>
    )
}

function Field({ field, draft, onChange, onClear }) {
    const isBool = field.type === 'bool'
    const dirty = draft !== undefined

    if (isBool) {
        const value = dirty ? draft : field.value
        return (
            <div className="flex items-center justify-between rounded border border-dark-700/60 px-3 py-2.5">
                <div>
                    <div className="text-xs text-dark-100">{field.label}</div>
                    {field.key === 'ANGELONE_ALLOW_TRADING' && (
                        <div className="mt-0.5 flex items-center gap-1 text-[10px] text-alert-400">
                            <AlertTriangle className="h-3 w-3" /> Enables real orders on a real account
                        </div>
                    )}
                </div>
                <button
                    onClick={() => onChange(!value)}
                    className={`relative h-5 w-9 rounded-full transition-colors ${
                        value ? 'bg-alert-500/70' : 'bg-dark-700'
                    }`}
                >
                    <span
                        className={`absolute top-0.5 h-4 w-4 rounded-full bg-dark-100 transition-transform ${
                            value ? 'translate-x-4' : 'translate-x-0.5'
                        }`}
                    />
                </button>
            </div>
        )
    }

    return (
        <div>
            <div className="mb-1 flex items-center justify-between">
                <label className="hud-label">{field.label}</label>
                <div className="flex items-center gap-2">
                    {field.is_set && (
                        <span className="badge-success">
                            <Check className="h-2.5 w-2.5" />
                            {field.hint || 'set'}
                        </span>
                    )}
                    {field.source && <span className="badge-muted">{field.source}</span>}
                </div>
            </div>
            <div className="flex gap-2">
                <input
                    type={field.secret ? 'password' : 'text'}
                    value={dirty ? draft : field.secret ? '' : (field.value ?? '')}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={
                        field.secret
                            ? field.is_set
                                ? 'Enter a new value to replace'
                                : 'Not set'
                            : 'Not set'
                    }
                    className="input-field"
                    autoComplete="off"
                />
                {dirty && (
                    <button onClick={onClear} className="btn-ghost !px-2.5" title="Discard">
                        <X className="h-3.5 w-3.5" />
                    </button>
                )}
            </div>
            {field.secret && field.is_set && (
                <p className="mt-1 text-[10px] text-dark-600">
                    Leave blank to keep. Save an empty value to remove it.
                </p>
            )}
        </div>
    )
}

function TestButton({ label, state, onClick }) {
    return (
        <button onClick={onClick} disabled={state?.running} className="btn-ghost">
            {state?.running ? (
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            ) : (
                <Plug className="h-3.5 w-3.5" />
            )}
            {label}
        </button>
    )
}

function TestResult({ name, result }) {
    const ok =
        result.connected === true ||
        result.valid === true ||
        result.local?.available === true ||
        result.cloud?.available === true

    return (
        <div
            className={`rounded border px-3 py-2 font-mono text-[11px] leading-relaxed ${
                ok
                    ? 'border-success-400/25 bg-success-500/8 text-success-300'
                    : 'border-danger-400/25 bg-danger-500/8 text-danger-300'
            }`}
        >
            <div className="mb-1 uppercase tracking-widest opacity-70">{name}</div>
            {name === 'ai' ? (
                <>
                    <div>local: {result.local?.available ? `ok — ${result.local.model}` : result.local?.error}</div>
                    <div>cloud: {result.cloud?.available ? `ok — ${result.cloud.model}` : result.cloud?.error}</div>
                </>
            ) : result.connected ? (
                <div>
                    Connected as {result.profile?.name || result.profile?.client_code} ·{' '}
                    {result.holdings_found} holdings · ₹{result.funds_available} available
                    {result.can_trade ? ' · TRADING ENABLED' : ' · read-only'}
                </div>
            ) : result.valid ? (
                <div>Connected{result.username ? ` as @${result.username}` : ''}</div>
            ) : (
                <div>{result.error || 'Not connected'}</div>
            )}
        </div>
    )
}

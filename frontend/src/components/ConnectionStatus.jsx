import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleDashed, RotateCw } from 'lucide-react'
import { setupApi } from '../services/api'

/**
 * What is actually connected, and what to do about what isn't.
 *
 * The reason this exists rather than a row of green/grey dots: a dot cannot
 * distinguish "you never added a key" from "your key is perfect but the
 * account has no credits" from "the bot works but Telegram won't let it
 * message you first". All three read as *not connected*, and the natural
 * response to all three is to re-enter a key — which fixes exactly one of them.
 *
 * So each row states the state, the reason, and the single next action.
 */

const STATE = {
    live: {
        Icon: CheckCircle2,
        tone: 'text-success-300',
        ring: 'border-success-400/30',
        label: 'Connected',
    },
    blocked: {
        Icon: AlertTriangle,
        tone: 'text-alert-300',
        ring: 'border-alert-400/35',
        label: 'Needs you',
    },
    missing: {
        Icon: CircleDashed,
        tone: 'text-dark-500',
        ring: 'border-dark-700/70',
        label: 'Not set up',
    },
    // Optional and deliberately unused (local-only AI, no broker): neither a
    // failure nor "connected".
    off: {
        Icon: CircleDashed,
        tone: 'text-dark-300',
        ring: 'border-dark-700/70',
        label: 'Off (optional)',
    },
}

export default function ConnectionStatus() {
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState(null)

    const load = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const response = await setupApi.diagnostics()
            setData(response.data)
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { load() }, [load])

    const summary = data?.summary
    const rows = Object.entries(data?.checks || {})

    return (
        <section className="hud-panel p-4">
            <header className="mb-3 flex items-center gap-3">
                <h2 className="hud-title">Connections</h2>
                {summary && (
                    <span className="type-caption vibrant-tertiary">
                        <span className="text-success-300">{summary.live} live</span>
                        {summary.blocked > 0 && (
                            <> · <span className="text-alert-300">{summary.blocked} need you</span></>
                        )}
                        {summary.missing > 0 && <> · {summary.missing} not set up</>}
                    </span>
                )}
                <button
                    onClick={load}
                    disabled={loading}
                    className="press material-control type-caption ml-auto flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-dark-200 disabled:opacity-40"
                >
                    <RotateCw size={11} className={loading ? 'animate-spin' : ''} />
                    Re-check
                </button>
            </header>

            {error && (
                <p className="type-body mb-3 text-danger-300">{error}</p>
            )}

            {!data && loading && (
                <p className="type-body vibrant-tertiary py-6 text-center">Checking…</p>
            )}

            <div className="space-y-1.5">
                {rows.map(([key, check]) => {
                    const style = STATE[check.state] || STATE.missing
                    const { Icon } = style
                    return (
                        <div
                            key={key}
                            className={`material-raised rounded-lg border ${style.ring} px-3 py-2.5`}
                        >
                            <div className="flex items-start gap-2.5">
                                <Icon size={14} className={`mt-0.5 shrink-0 ${style.tone}`} />
                                <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-baseline gap-x-2">
                                        <span className="type-body vibrant font-medium">
                                            {check.label}
                                        </span>
                                        <span className={`type-label ${style.tone}`}>
                                            {style.label}
                                        </span>
                                    </div>
                                    <p className="type-caption vibrant-secondary mt-0.5">
                                        {check.detail}
                                    </p>
                                    {check.action && (
                                        <p className="type-caption mt-1.5 text-primary-300/85">
                                            → {check.action}
                                        </p>
                                    )}
                                </div>
                            </div>
                        </div>
                    )
                })}
            </div>
        </section>
    )
}

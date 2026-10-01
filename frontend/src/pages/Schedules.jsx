import { useCallback, useEffect, useState } from 'react'
import { CalendarClock, Clock, Loader2, Plus, Trash2, X } from 'lucide-react'
import ResearchNav, { Banner, LocalModelNote, useEngine } from '../components/research/ResearchNav'
import Markdown from '../components/research/Markdown'
import { HudPanel, Empty, ErrorNote } from '../components/hud/HudPanel'
import { Chip, Field } from '../components/research/Controls'
import { researchApi } from '../services/api'

/**
 * Schedules: research the agent runs by itself on a timetable, such as the
 * India pick-of-the-day report at 09:45 IST. Tradeo keeps the engine running,
 * so a schedule fires as long as Tradeo is up.
 */

const PRESETS = [
    { label: 'Weekdays 09:45', cron: '45 9 * * 1-5' },
    { label: 'Weekdays 15:45 (after close)', cron: '45 15 * * 1-5' },
    { label: 'Every Saturday 09:00', cron: '0 9 * * 6' },
    { label: 'Every day 08:30', cron: '30 8 * * *' },
]

const MARKET = { in: '🇮🇳 India', us: 'US', cn: 'China', hk: 'Hong Kong', global: 'Global', crypto: 'Crypto' }

function describeCron(cron) {
    const hit = PRESETS.find((p) => p.cron === cron)
    if (hit) return hit.label
    const [m, h, , , dow] = (cron || '').split(' ')
    if (/^\d+$/.test(m) && /^\d+$/.test(h)) {
        const days = dow === '1-5' ? 'weekdays' : dow === '*' ? 'daily' : `days ${dow}`
        return `${days} at ${h.padStart(2, '0')}:${m.padStart(2, '0')}`
    }
    return cron
}

export default function Schedules() {
    const [engine, reloadEngine] = useEngine()
    const [playbooks, setPlaybooks] = useState([])
    const [jobs, setJobs] = useState([])
    const [status, setStatus] = useState(null)
    const [market, setMarket] = useState('in')
    const [creating, setCreating] = useState(null)
    const [error, setError] = useState(null)

    const load = useCallback(async () => {
        const [p, j, s] = await Promise.allSettled([researchApi.playbooks(), researchApi.schedules(), researchApi.scheduleStatus()])
        if (p.status === 'fulfilled') setPlaybooks(p.value.data || [])
        if (j.status === 'fulfilled') setJobs(j.value.data || [])
        if (s.status === 'fulfilled') setStatus(s.value.data)
    }, [])

    useEffect(() => {
        if (engine?.online) load()
    }, [engine?.online, load])

    const remove = async (id) => {
        try {
            await researchApi.deleteSchedule(id)
            load()
        } catch (e) {
            setError(e?.response?.data?.detail || e.message)
        }
    }

    const markets = [...new Set(playbooks.flatMap((p) => p.markets || []))]
    const shown = playbooks.filter((p) => !market || (p.markets || []).includes(market))

    return (
        <div className="space-y-4 animate-fade-in">
            <ResearchNav
                title="Schedules"
                subtitle="Research the agent runs by itself on a timetable. Results appear as sessions in the Agent tab and as runs."
                engine={engine}
                onEngineChange={reloadEngine}
                actions={
                    <button onClick={() => setCreating({ custom: true })} disabled={!engine?.online} className="btn-primary flex items-center gap-2 !py-1.5 text-xs">
                        <Plus className="h-3.5 w-3.5" /> Custom schedule
                    </button>
                }
            />
            <LocalModelNote engine={engine} />
            {status && !status.enabled && (
                <Banner>The scheduler is off. Restart the engine (↻) so Tradeo can turn it on.</Banner>
            )}
            <ErrorNote error={error} onRetry={() => setError(null)} />

            <HudPanel
                title="Active schedules"
                subtitle={status ? `scheduler ${status.running ? 'running' : status.enabled ? 'enabled' : 'off'}` : ''}
                padded={false}
            >
                {jobs.length === 0 ? (
                    <Empty icon={CalendarClock} title="Nothing scheduled" hint="Pick a playbook below, or write your own." />
                ) : (
                    <div className="divide-y divide-dark-800/70">
                        {jobs.map((j) => (
                            <div key={j.id} className="flex items-center gap-4 px-4 py-3">
                                <Clock className="h-4 w-4 shrink-0 text-primary-400" />
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-sm text-dark-100">{j.title || j.id}</div>
                                    <div className="font-mono text-[11px] text-dark-500">
                                        {describeCron(j.schedule)} · {j.timezone || 'local'}
                                        {j.next_run_at && ` · next ${new Date(j.next_run_at * (j.next_run_at > 1e12 ? 1 : 1000)).toLocaleString('en-IN')}`}
                                    </div>
                                </div>
                                {j.last_status && <span className="badge-muted">{j.last_status}</span>}
                                <button onClick={() => remove(j.id)} className="text-dark-500 hover:text-danger-400" title="Delete">
                                    <Trash2 className="h-4 w-4" />
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </HudPanel>

            <div className="flex flex-wrap items-center gap-1.5">
                <span className="hud-label mr-1">Playbooks</span>
                <Chip active={!market} onClick={() => setMarket('')}>all</Chip>
                {markets.map((m) => <Chip key={m} active={market === m} onClick={() => setMarket(m)}>{MARKET[m] || m}</Chip>)}
            </div>

            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {shown.map((p, i) => (
                    <button
                        key={p.slug}
                        onClick={() => setCreating(p)}
                        disabled={!engine?.online}
                        style={{ animationDelay: `${i * 40}ms` }}
                        className="hud-panel group animate-slide-up p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary-400/40 hover:shadow-glow disabled:opacity-50"
                    >
                        <div className="flex items-start justify-between gap-2">
                            <h3 className="font-semibold text-dark-50 group-hover:text-primary-200">{p.name}</h3>
                            <span className="shrink-0 font-mono text-[10px] text-dark-500">{describeCron(p.suggested_schedule)}</span>
                        </div>
                        <p className="mt-2 line-clamp-3 text-xs text-dark-400">{p.description}</p>
                        <div className="mt-3 flex flex-wrap gap-1">
                            {(p.markets || []).map((m) => <span key={m} className="badge-muted">{MARKET[m] || m}</span>)}
                        </div>
                    </button>
                ))}
            </div>

            {creating && (
                <CreateDialog
                    playbook={creating.custom ? null : creating}
                    onClose={() => setCreating(null)}
                    onDone={() => {
                        setCreating(null)
                        load()
                    }}
                    onError={(e) => setError(e)}
                />
            )}
        </div>
    )
}

function CreateDialog({ playbook, onClose, onDone, onError }) {
    const [full, setFull] = useState(null)
    const [title, setTitle] = useState(playbook?.name || '')
    const [prompt, setPrompt] = useState('')
    const [cron, setCron] = useState(playbook?.suggested_schedule || '45 9 * * 1-5')
    const [tz, setTz] = useState(playbook?.suggested_timezone || 'Asia/Kolkata')
    const [vars, setVars] = useState({})
    const [busy, setBusy] = useState(false)

    useEffect(() => {
        if (!playbook) return
        researchApi.playbook(playbook.slug).then((r) => {
            setFull(r.data)
            setVars(r.data.variables || {})
        }).catch(() => {})
    }, [playbook])

    const submit = async (e) => {
        e.preventDefault()
        setBusy(true)
        try {
            if (playbook) await researchApi.scheduleFromPlaybook(playbook.slug, { schedule: cron, timezone: tz, title, variables: vars, config: {} })
            else await researchApi.schedule({ prompt, title, schedule: cron, timezone: tz, config: {} })
            onDone()
        } catch (err) {
            const d = err?.response?.data?.detail
            onError(Array.isArray(d) ? d.map((x) => x.msg).join('; ') : d || err.message)
            setBusy(false)
        }
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-dark-950/70 p-4 backdrop-blur-sm animate-fade-in" onClick={onClose}>
            <form onSubmit={submit} className="hud-panel-glow max-h-[90vh] w-full max-w-2xl overflow-y-auto p-5 animate-slide-up" onClick={(e) => e.stopPropagation()}>
                <div className="flex items-start justify-between">
                    <div>
                        <div className="hud-label">{playbook ? 'Schedule playbook' : 'Custom schedule'}</div>
                        <h2 className="mt-1 text-lg font-semibold text-dark-50">{playbook?.name || 'Your own research task'}</h2>
                    </div>
                    <button type="button" onClick={onClose} className="text-dark-500 hover:text-dark-100"><X className="h-5 w-5" /></button>
                </div>

                <div className="mt-4 space-y-3">
                    <Field label="Title">
                        <input value={title} onChange={(e) => setTitle(e.target.value)} className="input-field w-full" required />
                    </Field>
                    {!playbook && (
                        <Field label="What should the agent do?">
                            <textarea
                                rows={5}
                                value={prompt}
                                onChange={(e) => setPrompt(e.target.value)}
                                placeholder="e.g. Check Tradeo's watchtower opportunities and today's news, and summarise the three most interesting NSE setups."
                                className="input-field w-full"
                                required
                            />
                        </Field>
                    )}
                    <div>
                        <span className="hud-label mb-1 block">When</span>
                        <div className="mb-2 flex flex-wrap gap-1.5">
                            {PRESETS.map((p) => <Chip key={p.cron} active={cron === p.cron} onClick={() => setCron(p.cron)}>{p.label}</Chip>)}
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                            <input value={cron} onChange={(e) => setCron(e.target.value)} className="input-field font-mono" title="cron: minute hour day month weekday" />
                            <input value={tz} onChange={(e) => setTz(e.target.value)} className="input-field font-mono" />
                        </div>
                        <p className="mt-1 text-[11px] text-dark-500">{describeCron(cron)} ({tz})</p>
                    </div>
                    {Object.keys(vars).length > 0 && Object.entries(vars).map(([k, v]) => (
                        <Field key={k} label={k}>
                            <input value={v} onChange={(e) => setVars({ ...vars, [k]: e.target.value })} className="input-field w-full" />
                        </Field>
                    ))}
                    {full?.body && (
                        <details className="rounded border border-dark-800 p-3">
                            <summary className="cursor-pointer text-xs text-dark-400 hover:text-primary-300">What the agent will be told</summary>
                            <div className="mt-2 max-h-64 overflow-y-auto text-dark-300"><Markdown className="!text-xs">{full.body}</Markdown></div>
                        </details>
                    )}
                    <button disabled={busy} className="btn-primary flex w-full items-center justify-center gap-2 disabled:opacity-50">
                        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CalendarClock className="h-4 w-4" />} Schedule it
                    </button>
                </div>
            </form>
        </div>
    )
}

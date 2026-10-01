import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { BrainCircuit, CalendarClock, Network, Sigma } from 'lucide-react'
import { HudPanel, StatusDot } from '../hud/HudPanel'
import { researchApi } from '../../services/api'

/** The research lab at a glance, for the command deck's status rail. */
export default function ResearchRail() {
    const [status, setStatus] = useState(null)
    const [sessions, setSessions] = useState([])
    const [schedules, setSchedules] = useState([])

    useEffect(() => {
        let alive = true
        const load = async () => {
            try {
                const s = (await researchApi.status()).data
                if (!alive) return
                setStatus(s)
                if (!s.online) return
                const [ss, sc] = await Promise.allSettled([researchApi.sessions(3), researchApi.schedules()])
                if (!alive) return
                if (ss.status === 'fulfilled') setSessions(ss.value.data || [])
                if (sc.status === 'fulfilled') setSchedules(sc.value.data || [])
            } catch {
                if (alive) setStatus({ online: false })
            }
        }
        load()
        const timer = setInterval(load, 30000)
        return () => {
            alive = false
            clearInterval(timer)
        }
    }, [])

    return (
        <HudPanel
            title="Research lab"
            corners={false}
            right={<StatusDot status={status?.online ? 'ok' : status ? 'error' : 'idle'} pulse={!!status?.online} />}
        >
            <div className="space-y-3">
                {status?.online && (
                    <p className="font-mono text-[10px] text-dark-500">{status.llm?.provider} · {status.llm?.model}</p>
                )}
                <div className="grid grid-cols-4 gap-1">
                    {[
                        { to: '/research', icon: BrainCircuit, label: 'Agent' },
                        { to: '/research/swarm', icon: Network, label: 'Swarm' },
                        { to: '/research/alpha', icon: Sigma, label: 'Alphas' },
                        { to: '/research/schedules', icon: CalendarClock, label: 'Plans' },
                    ].map((a) => (
                        <Link
                            key={a.to}
                            to={a.to}
                            className="press flex flex-col items-center gap-1 rounded border border-dark-800 py-2 text-[10px] text-dark-400 transition-all hover:border-primary-400/40 hover:text-primary-200"
                        >
                            <a.icon className="h-4 w-4" />
                            {a.label}
                        </Link>
                    ))}
                </div>
                {sessions.length > 0 && (
                    <div className="space-y-1">
                        <div className="hud-label">Recent</div>
                        {sessions.map((s) => (
                            <Link key={s.session_id} to="/research" className="block truncate text-[11px] text-dark-400 hover:text-primary-200">
                                {(s.title || 'Untitled').replace(/^#+\s*/, '')}
                            </Link>
                        ))}
                    </div>
                )}
                {schedules.length > 0 && (
                    <p className="text-[11px] text-dark-500">{schedules.length} scheduled task{schedules.length === 1 ? '' : 's'} active</p>
                )}
            </div>
        </HudPanel>
    )
}

import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { HudPanel } from './hud/HudPanel'
import { setupApi } from '../services/api'

/**
 * Failures since the backend started (core/failures.py). Tradeo keeps running
 * when a data source or an agent run fails; this is where that becomes visible
 * instead of looking like a quiet market.
 */

const TONE = { ok: 'text-success-300', degraded: 'text-alert-300', failing: 'text-danger-300' }

export default function FailureCounts() {
    const [data, setData] = useState(null)

    useEffect(() => {
        const load = () => setupApi.failures().then(({ data }) => setData(data)).catch(() => {})
        load()
        const timer = setInterval(load, 60000)
        return () => clearInterval(timer)
    }, [])

    const rows = Object.entries(data?.components || {}).filter(([, c]) => c.failures > 0)
    return (
        <HudPanel title="Failures" subtitle="Data sources, news, the cloud checker and agent runs that failed since the backend started.">
            {rows.length === 0 ? (
                <p className="flex items-center gap-2 text-xs text-success-300">
                    <CheckCircle2 className="h-4 w-4" /> No failures recorded.
                </p>
            ) : (
                <ul className="space-y-1.5 text-xs">
                    {rows.map(([name, c]) => (
                        <li key={name} className="flex flex-wrap items-baseline justify-between gap-2 border-b border-dark-800/50 pb-1.5">
                            <span className={`flex items-center gap-1.5 ${TONE[c.state]}`}>
                                <AlertTriangle className="h-3.5 w-3.5" /> {name}
                                <span className="text-dark-500">({c.state})</span>
                            </span>
                            <span className="text-dark-400">{c.failures} failed · {c.successes} ok</span>
                            {c.last_error && <span className="w-full truncate text-[11px] text-dark-500">{c.last_error}</span>}
                        </li>
                    ))}
                </ul>
            )}
        </HudPanel>
    )
}

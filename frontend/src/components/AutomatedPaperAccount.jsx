import { useEffect, useState } from 'react'
import { Loading } from './hud/HudPanel'
import { PaperDetails } from './PortfolioCard'
import { wealthApi } from '../services/api'

/**
 * The automated paper account, whole: every agent that trades it, every
 * position with who opened it and why, every closed trade. Same data as the
 * Command Deck's Portfolio card, so the two screens always agree.
 */
export default function AutomatedPaperAccount() {
    const [paper, setPaper] = useState(null)

    useEffect(() => {
        const load = () => wealthApi.overview().then(({ data }) => setPaper(data.paper)).catch(() => {})
        load()
        const timer = setInterval(load, 30000)
        return () => clearInterval(timer)
    }, [])

    return (
        <div className="glass-card p-6">
            {paper ? <PaperDetails paper={paper} /> : <Loading rows={3} label="Paper account" />}
        </div>
    )
}

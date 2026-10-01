import { useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import BootSequence from './hud/BootSequence'
import CommandPalette from './hud/CommandPalette'
import Toasts from './hud/Toasts'
import { signalsApi, systemApi } from '../services/api'

/**
 * The shell.
 *
 * Ambient layers (grid, scanline, vignette) live here so every page inherits
 * the same instrument surface without re-declaring it.
 */

export default function Layout() {
    const location = useLocation()
    const [booted, setBooted] = useState(() => sessionStorage.getItem('tradeo.booted') === '1')
    const [health, setHealth] = useState(null)

    useEffect(() => {
        let alive = true
        const poll = async () => {
            try {
                const [h, m] = await Promise.allSettled([systemApi.health(), signalsApi.market()])
                if (!alive) return
                setHealth({
                    ...(h.status === 'fulfilled' ? h.value : {}),
                    market_open: m.status === 'fulfilled' ? m.value.data.is_open : false,
                })
            } catch {
                if (alive) setHealth({ status: 'unreachable' })
            }
        }
        poll()
        const timer = setInterval(poll, 30000)
        return () => {
            alive = false
            clearInterval(timer)
        }
    }, [])

    if (!booted) {
        return (
            <BootSequence
                onComplete={() => {
                    // Once per browser session — charming the first time,
                    // tedious on every navigation.
                    sessionStorage.setItem('tradeo.booted', '1')
                    setBooted(true)
                }}
            />
        )
    }

    return (
        <div className="relative flex h-screen overflow-hidden">
            {/* Ambient layers — pointer-events-none so they never eat clicks */}
            <div className="hud-grid pointer-events-none fixed inset-0 opacity-[0.35]" />
            <div
                className="pointer-events-none fixed inset-x-0 top-0 z-10 h-24 animate-scan opacity-30"
                style={{
                    background:
                        'linear-gradient(to bottom, transparent, rgba(34,211,238,0.05), transparent)',
                }}
            />
            <div
                className="pointer-events-none fixed inset-0 z-10"
                style={{
                    background: 'radial-gradient(ellipse at center, transparent 55%, rgba(5,10,20,0.75) 100%)',
                }}
            />

            <div className="aurora" aria-hidden="true" />

            <Sidebar
                health={health}
                brainState={health?.ai?.online === false ? 'offline' : 'idle'}
            />

            <main className="relative z-20 flex-1 overflow-y-auto">
                {/* Keyed by path: every screen gets its entrance animation. */}
                <div key={location.pathname} className="page-enter mx-auto h-full max-w-[1600px] p-6">
                    <Outlet />
                </div>
            </main>

            {/* Global surfaces. Mounted at the shell so ⌘K works from every
                screen and a toast raised during navigation survives the
                unmount of whatever raised it. */}
            <CommandPalette />
            <Toasts />
        </div>
    )
}

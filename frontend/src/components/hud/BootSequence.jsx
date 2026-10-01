import { useEffect, useRef, useState } from 'react'
import CoreOrb from './CoreOrb'
import { systemApi } from '../../services/api'

/**
 * Boot sequence.
 *
 * It is theatre, but useful theatre: the local model takes a few seconds to
 * page in, and this fills that window with the actual subsystem status instead
 * of a blank screen. Skippable, and only shown once per session.
 */

const STEPS = [
    { label: 'Initialising core', ms: 320 },
    { label: 'Loading instrument universe', ms: 260 },
    { label: 'Connecting reasoning engine', ms: 420, check: 'ai' },
    { label: 'Linking broker adapters', ms: 300 },
    { label: 'Arming watchtower', ms: 280 },
    { label: 'Calibrating voice interface', ms: 240 },
]

export default function BootSequence({ onComplete }) {
    const [step, setStep] = useState(0)
    const [health, setHealth] = useState(null)
    const [done, setDone] = useState(false)
    const completed = useRef(false)

    const finish = () => {
        if (completed.current) return
        completed.current = true
        onComplete()
    }

    useEffect(() => {
        systemApi.health().then(setHealth).catch(() => setHealth({ status: 'unreachable' }))
    }, [])

    useEffect(() => {
        if (step >= STEPS.length) {
            setDone(true)
            const timer = setTimeout(finish, 550)
            return () => clearTimeout(timer)
        }
        const timer = setTimeout(() => setStep((s) => s + 1), STEPS[step].ms)
        return () => clearTimeout(timer)
    }, [step])

    // Escape skips — nobody should be held hostage by an animation.
    useEffect(() => {
        const handler = (e) => e.key === 'Escape' && finish()
        window.addEventListener('keydown', handler)
        return () => window.removeEventListener('keydown', handler)
    }, [])

    const unreachable = health?.status === 'unreachable'

    return (
        <div className="relative flex h-screen flex-col items-center justify-center overflow-hidden">
            <div className="hud-grid pointer-events-none absolute inset-0 opacity-30" />

            <CoreOrb state={done ? 'idle' : 'thinking'} size={220} showLabel={false} />

            <h1 className="mt-8 font-mono text-2xl uppercase tracking-[0.5em] text-primary-300 text-glow animate-flicker">
                Tradeo
            </h1>
            <p className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.3em] text-dark-500">
                Multi-asset market intelligence
            </p>

            <div className="mt-10 w-full max-w-sm space-y-1.5 px-6">
                {STEPS.map((s, i) => (
                    <div
                        key={s.label}
                        className={`flex items-center justify-between font-mono text-[11px] transition-opacity duration-300 ${
                            i < step ? 'opacity-100' : i === step ? 'opacity-70' : 'opacity-25'
                        }`}
                    >
                        <span className="text-dark-400">{s.label}</span>
                        <span
                            className={
                                i < step
                                    ? unreachable && s.check === 'ai'
                                        ? 'text-danger-400'
                                        : 'text-success-400'
                                    : 'text-dark-600'
                            }
                        >
                            {i < step ? (unreachable && s.check === 'ai' ? 'FAIL' : 'OK') : '····'}
                        </span>
                    </div>
                ))}
            </div>

            {unreachable && step >= STEPS.length && (
                <p className="mt-6 max-w-sm px-6 text-center text-xs leading-relaxed text-danger-400">
                    Backend unreachable at localhost:8000. Start it with{' '}
                    <span className="font-mono text-danger-300">scripts/backend.sh start</span>.
                </p>
            )}

            <button
                onClick={finish}
                className="mt-10 font-mono text-[10px] uppercase tracking-[0.25em] text-dark-600 transition-colors hover:text-primary-300"
            >
                {done ? 'Enter' : 'Skip'} →
            </button>
        </div>
    )
}

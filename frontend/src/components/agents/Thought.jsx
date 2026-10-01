import { memo, useEffect, useRef, useState } from 'react'
import { ChevronRight, Eye, GitBranch, Lightbulb, Target, AlertTriangle } from 'lucide-react'

/**
 * One line of visible reasoning.
 *
 * Two deliberate choices:
 *
 * **It materializes rather than fades.** A thought arriving should read as
 * something resolving into focus — blur and scale settling together — not as a
 * decal being turned up. The blur is what makes it feel like the system is
 * forming the thought rather than revealing a prewritten one.
 *
 * **Plan lines recede.** A trace where every line shouts is a trace nobody
 * reads. Plans are scaffolding; observations, conflicts and readings are the
 * content. So plans sit at tertiary vibrancy and smaller type, and the eye
 * skips them until it wants them.
 */

const LEVELS = {
    plan: {
        Icon: ChevronRight,
        accent: 'text-dark-500',
        text: 'vibrant-tertiary',
        rail: 'transparent',
    },
    observe: {
        Icon: Eye,
        accent: 'text-primary-300/80',
        text: 'vibrant-secondary',
        rail: 'rgba(34,211,238,0.35)',
    },
    infer: {
        Icon: Lightbulb,
        accent: 'text-violet-300/85',
        text: 'vibrant',
        rail: 'rgba(167,139,250,0.45)',
    },
    conflict: {
        Icon: GitBranch,
        accent: 'text-alert-300',
        text: 'vibrant',
        rail: 'rgba(251,191,36,0.6)',
    },
    decide: {
        Icon: Target,
        accent: 'text-success-300',
        text: 'vibrant',
        rail: 'rgba(74,222,128,0.6)',
    },
    warn: {
        Icon: AlertTriangle,
        accent: 'text-danger-300',
        text: 'vibrant',
        rail: 'rgba(248,113,113,0.6)',
    },
}

function ThoughtRow({ thought, isNew }) {
    const level = LEVELS[thought.level] || LEVELS.plan
    const { Icon } = level
    const [entered, setEntered] = useState(!isNew)
    const raf = useRef(0)

    useEffect(() => {
        if (entered) return undefined
        // Two frames: one to commit the pre-state, one to start the transition.
        // A single frame occasionally coalesces and the element appears without
        // ever animating.
        raf.current = requestAnimationFrame(() =>
            requestAnimationFrame(() => setEntered(true))
        )
        return () => cancelAnimationFrame(raf.current)
    }, [entered])

    const emphasis = thought.level === 'plan'

    return (
        <div
            className="group relative flex gap-2.5 pl-3"
            style={{
                opacity: entered ? 1 : 0,
                transform: entered ? 'translateY(0) scale(1)' : 'translateY(8px) scale(0.985)',
                filter: entered ? 'blur(0)' : 'blur(4px)',
                transition:
                    'opacity 380ms cubic-bezier(0.16,1,0.3,1), ' +
                    'transform 420ms cubic-bezier(0.16,1,0.3,1), ' +
                    'filter 320ms cubic-bezier(0.16,1,0.3,1)',
            }}
        >
            {/* A rail instead of a bullet: it groups visually down the column
                and lets severity be read as a colour edge, peripherally. */}
            <span
                aria-hidden
                className="absolute left-0 top-1 h-[calc(100%-0.35rem)] w-[2px] rounded-full"
                style={{ background: level.rail }}
            />
            <Icon
                size={emphasis ? 11 : 13}
                className={`mt-[3px] shrink-0 ${level.accent}`}
                strokeWidth={emphasis ? 2 : 2.2}
            />
            <p className={`${emphasis ? 'type-caption' : 'type-body'} ${level.text}`}>
                {thought.text}
            </p>
        </div>
    )
}

export default memo(ThoughtRow)

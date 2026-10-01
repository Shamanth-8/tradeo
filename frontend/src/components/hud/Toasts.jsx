import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { useToastStore } from '../../store/toast'

/**
 * The notification stack.
 *
 * Bottom-right rather than top-centre: the top of this app is where the market
 * clock and the assistant's own state live, and a toast landing there covers
 * exactly the readouts someone is most likely watching when a long run
 * finishes.
 */

const TONES = {
    ok: { icon: CheckCircle2, ring: 'border-success-400/35', accent: 'text-success-400',
          glow: 'shadow-[0_0_28px_-10px_rgba(74,222,128,0.5)]' },
    info: { icon: Info, ring: 'border-primary-400/35', accent: 'text-primary-300',
            glow: 'shadow-glow' },
    warn: { icon: AlertTriangle, ring: 'border-alert-400/40', accent: 'text-alert-300',
            glow: 'shadow-glow-alert' },
    error: { icon: XCircle, ring: 'border-danger-400/45', accent: 'text-danger-300',
             glow: 'shadow-glow-danger' },
}

export default function Toasts() {
    const toasts = useToastStore((state) => state.toasts)
    const dismiss = useToastStore((state) => state.dismiss)

    if (!toasts.length) return null

    return (
        <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-[min(380px,calc(100vw-2.5rem))] flex-col gap-2">
            {toasts.map((toast) => {
                const tone = TONES[toast.tone] || TONES.info
                const Icon = tone.icon
                return (
                    <div
                        key={toast.id}
                        role={toast.tone === 'error' ? 'alert' : 'status'}
                        className={`materialize pointer-events-auto flex items-start gap-2.5 rounded-lg border ${tone.ring} ${tone.glow} material-panel px-3.5 py-2.5`}
                    >
                        <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${tone.accent}`} />
                        <div className="min-w-0 flex-1">
                            <p className="text-xs leading-relaxed vibrant">{toast.message}</p>
                            {toast.detail && (
                                <p className="mt-0.5 font-mono text-[10px] leading-relaxed vibrant-tertiary">
                                    {toast.detail}
                                </p>
                            )}
                        </div>
                        <button
                            onClick={() => dismiss(toast.id)}
                            className="press shrink-0 rounded p-0.5 text-dark-500 transition-colors hover:text-dark-200"
                            aria-label="Dismiss"
                        >
                            <X className="h-3.5 w-3.5" />
                        </button>
                    </div>
                )
            })}
        </div>
    )
}

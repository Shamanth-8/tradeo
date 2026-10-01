import { Component } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'

/**
 * Stops one broken component taking the whole app with it.
 *
 * This exists because it already happened: a run paused for approval served an
 * empty verdict object, a chart component called `.toFixed` on `undefined`, and
 * React unmounted the entire tree — a blank screen, with the real error only
 * visible in the console.
 *
 * A trading interface must never fail silently to black. Failing loudly in one
 * panel, while the rest of the screen keeps working, is the difference between
 * a bug and an outage.
 */
export default class ErrorBoundary extends Component {
    constructor(props) {
        super(props)
        this.state = { error: null }
    }

    static getDerivedStateFromError(error) {
        return { error }
    }

    componentDidCatch(error, info) {
        // Keep the stack somewhere a human can find it without a debugger.
        console.error('[Tradeo] component failed:', error, info?.componentStack)
    }

    render() {
        const { error } = this.state
        if (!error) return this.props.children

        return (
            <div className="material-panel m-4 rounded-xl border-danger-500/35 p-4">
                <p className="type-label mb-2 flex items-center gap-2 text-danger-300">
                    <AlertTriangle size={14} />
                    {this.props.label || 'This panel failed'}
                </p>
                <p className="type-body vibrant-secondary mb-3">
                    {String(error?.message || error)}
                </p>
                <button
                    onClick={() => this.setState({ error: null })}
                    className="press material-control type-caption flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-dark-200"
                >
                    <RotateCw size={12} /> Retry
                </button>
            </div>
        )
    }
}

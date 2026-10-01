import { useEffect, useRef, useState } from 'react'

/**
 * Springs, parameterised the way Apple parameterises them.
 *
 * Not `mass / stiffness / damping` — those are physics, and nobody designs in
 * them. Two numbers instead:
 *
 *   response      how quickly the value reaches the target, in seconds.
 *                 Not a duration: a spring has no fixed duration, its settle
 *                 time emerges from the parameters.
 *   dampingRatio  overshoot. 1.0 is critically damped — arrives and stops.
 *                 Below 1.0 it overshoots and oscillates; lower is bouncier.
 *
 * House rule, from the Apple guidance: default everything to 1.0. Reserve
 * bounce for motion that had momentum behind it — a flick, a drag release.
 * Overshoot on a panel that merely appeared feels wrong; overshoot on a card
 * you threw feels right.
 *
 * The important property is that this is **interruptible**. Retargeting mid
 * flight keeps the current position *and* the current velocity, so a reversal
 * bends the motion instead of hitting a brick wall. CSS transitions cannot do
 * this: they restart from a fixed curve and produce a visible discontinuity.
 */

// Apple's shipped values, so call sites read as intent rather than numbers.
export const SPRING = {
    // Default UI motion — arrives, settles, does not draw attention.
    default: { response: 0.4, dampingRatio: 1.0 },
    // Snappier, for small elements and immediate feedback.
    snappy: { response: 0.28, dampingRatio: 1.0 },
    // Sheets and drawers — a little overshoot, because they are dragged.
    drawer: { response: 0.3, dampingRatio: 0.8 },
    // Momentum releases.
    flick: { response: 0.4, dampingRatio: 0.8 },
    // Slow, for large surfaces where fast movement would be violent.
    gentle: { response: 0.6, dampingRatio: 1.0 },
}

function prefersReducedMotion() {
    if (typeof window === 'undefined' || !window.matchMedia) return false
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * Integrate one spring step.
 *
 * Semi-implicit Euler, substepped so that a dropped frame (a long dt) cannot
 * make the integration explode — clamping dt alone would silently slow the
 * animation down instead.
 */
function step(state, target, response, dampingRatio, dt) {
    const omega = (2 * Math.PI) / response
    const substeps = Math.max(1, Math.ceil(dt / (1 / 120)))
    const h = dt / substeps

    let { value, velocity } = state
    for (let i = 0; i < substeps; i += 1) {
        const acceleration =
            -2 * dampingRatio * omega * velocity - omega * omega * (value - target)
        velocity += acceleration * h
        value += velocity * h
    }
    return { value, velocity }
}

/**
 * A single spring-driven number.
 *
 * Returns the live value. Set a new target at any time — including mid-flight —
 * and the motion continues from wherever it currently is, at whatever speed it
 * currently has.
 */
export function useSpringValue(target, options = {}) {
    const {
        response = SPRING.default.response,
        dampingRatio = SPRING.default.dampingRatio,
        precision = 0.01,
        initial = target,
        velocity: initialVelocity = 0,
    } = options

    const [value, setValue] = useState(initial)
    const state = useRef({ value: initial, velocity: initialVelocity })
    const frame = useRef(0)
    const last = useRef(0)

    useEffect(() => {
        // Reduced motion means no vestibular travel — snap, don't animate.
        // The value still changes, so meaning is preserved; only the journey
        // is removed.
        if (prefersReducedMotion()) {
            state.current = { value: target, velocity: 0 }
            setValue(target)
            return undefined
        }

        last.current = 0

        const tick = (now) => {
            const dt = last.current ? Math.min((now - last.current) / 1000, 0.064) : 1 / 60
            last.current = now

            const next = step(state.current, target, response, dampingRatio, dt)
            state.current = next

            const settled =
                Math.abs(next.value - target) < precision && Math.abs(next.velocity) < precision
            if (settled) {
                state.current = { value: target, velocity: 0 }
                setValue(target)
                return
            }

            setValue(next.value)
            frame.current = requestAnimationFrame(tick)
        }

        frame.current = requestAnimationFrame(tick)
        return () => cancelAnimationFrame(frame.current)
    }, [target, response, dampingRatio, precision])

    return value
}

/**
 * Momentum projection — where a flick is *going*, not where it was released.
 *
 * This is Apple's exponential-decay form from the Designing Fluid Interfaces
 * sample code, deliberately not the textbook v²/2a. Snapping to the nearest
 * target from the release point makes a flick feel dead; projecting first is
 * what makes it feel thrown.
 */
export function project(velocity, decelerationRate = 0.998) {
    return ((velocity / 1000) * decelerationRate) / (1 - decelerationRate)
}

/**
 * Progressive resistance past a boundary.
 *
 * A hard stop reads as frozen. Resistance that grows the further you pull
 * reads as responsive-but-empty, which is the truth.
 */
export function rubberband(overshoot, dimension, constant = 0.55) {
    return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot))
}

/**
 * Tracks pointer velocity over a short history.
 *
 * The instantaneous delta between the last two events is far too noisy to hand
 * to a spring; a short window smooths it without adding perceptible lag.
 */
export function createVelocityTracker(windowMs = 100) {
    let samples = []
    return {
        add(position) {
            const now = performance.now()
            samples.push({ position, at: now })
            samples = samples.filter((s) => now - s.at <= windowMs)
        },
        velocity() {
            if (samples.length < 2) return 0
            const first = samples[0]
            const last = samples[samples.length - 1]
            const elapsed = (last.at - first.at) / 1000
            return elapsed > 0 ? (last.position - first.position) / elapsed : 0
        },
        reset() {
            samples = []
        },
    }
}

export default useSpringValue

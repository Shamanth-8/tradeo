import { useEffect, useRef, useState } from 'react'

/**
 * Counts a displayed value up (or down) to its new number, keeping its exact
 * format: "₹10,00,394", "+0.04%", "45.6%", "−4.99%", "2,059". Strings with no
 * number are shown as they are. Off under prefers-reduced-motion.
 */

const NUMBER = /[-+−]?\d[\d,]*(\.\d+)?/

function parse(text) {
    const m = String(text ?? '').match(NUMBER)
    if (!m) return null
    const raw = m[0]
    const negative = raw.startsWith('-') || raw.startsWith('−')
    const digits = raw.replace(/[^\d.]/g, '')
    return {
        value: (negative ? -1 : 1) * parseFloat(digits),
        decimals: (digits.split('.')[1] || '').length,
        indian: /\d,\d\d,\d{3}/.test(raw) || /^[-+−]?\d{1,2},\d{2},/.test(raw),
        grouped: raw.includes(','),
        sign: raw[0] === '+' ? '+' : raw[0] === '−' ? '−' : raw[0] === '-' ? '-' : '',
        before: String(text).slice(0, m.index),
        after: String(text).slice(m.index + raw.length),
    }
}

function format(n, p) {
    const abs = Math.abs(n)
    let body = abs.toFixed(p.decimals)
    if (p.grouped) {
        body = abs.toLocaleString(p.indian ? 'en-IN' : 'en-US', {
            minimumFractionDigits: p.decimals, maximumFractionDigits: p.decimals,
        })
    }
    const sign = n < 0 ? (p.sign === '−' ? '−' : '-') : p.sign === '+' ? '+' : ''
    return `${p.before}${sign}${body}${p.after}`
}

const reduced = () =>
    typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export default function AnimatedValue({ value, duration = 700 }) {
    const target = parse(value)
    const [shown, setShown] = useState(target ? format(0, target) : value)
    const from = useRef(0)

    useEffect(() => {
        const p = parse(value)
        if (!p || reduced()) {
            setShown(value)
            if (p) from.current = p.value
            return undefined
        }
        const start = performance.now()
        const begin = from.current
        let raf
        const tick = (now) => {
            const t = Math.min(1, (now - start) / duration)
            const eased = 1 - Math.pow(1 - t, 3)
            setShown(t >= 1 ? value : format(begin + (p.value - begin) * eased, p))
            if (t < 1) raf = requestAnimationFrame(tick)
            else from.current = p.value
        }
        raf = requestAnimationFrame(tick)
        return () => cancelAnimationFrame(raf)
    }, [value, duration])

    return <>{shown}</>
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

/**
 * A Python editor built from a textarea and a highlighted <pre>.
 *
 * No CodeMirror, no Monaco. Monaco is ~2 MB and would more than double the
 * bundle of an app whose whole premise is running on your own machine without
 * a stack behind it. What a strategy editor actually needs is a short list —
 * highlighting, line numbers, sane indentation, and an error marker on the
 * line that failed — and all four are cheaper to build than to import.
 *
 * The technique is the standard one: a transparent textarea holds the text and
 * the caret, a <pre> underneath paints the colours, and the two are kept in
 * exact alignment by sharing every metric that affects layout — font, size,
 * line height, padding, tab size and white-space handling. Any divergence
 * shows up immediately as drifting colour, which is why those values live in
 * one constant rather than in two class lists.
 */

const SHARED =
    'font-mono text-[13px] leading-[1.55] tracking-normal'

// Kept as an object so the textarea and the <pre> cannot disagree about a
// value that would misalign them.
const METRICS = {
    padding: '12px 14px',
    tabSize: 4,
    whiteSpace: 'pre',
    fontFamily: "'JetBrains Mono', ui-monospace, monospace",
    fontSize: '13px',
    lineHeight: '1.55',
}

const KEYWORDS = new Set([
    'and', 'as', 'assert', 'break', 'class', 'continue', 'def', 'del', 'elif',
    'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in',
    'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try',
    'while', 'with', 'yield', 'None', 'True', 'False', 'self',
])

const BUILTINS = new Set([
    'abs', 'all', 'any', 'bool', 'dict', 'enumerate', 'filter', 'float', 'int',
    'len', 'list', 'max', 'min', 'range', 'round', 'set', 'sorted', 'str',
    'sum', 'tuple', 'zip', 'sqrt', 'log', 'exp', 'floor', 'ceil', 'isnan', 'pi',
])

// Recognised so a typo in a method name is visible before the run fails.
const CTX_METHODS = new Set([
    'price', 'open', 'high', 'low', 'close', 'volume', 'date', 'bar', 'closes',
    'highs', 'lows', 'opens', 'volumes', 'sma', 'ema', 'rsi', 'atr', 'adx',
    'macd', 'bollinger', 'bb_width', 'stochastic', 'vwap', 'zscore', 'highest',
    'lowest', 'stdev', 'roc', 'volume_ratio', 'crossed_above', 'crossed_below',
    'param', 'params', 'log', 'buy', 'sell', 'short', 'close_position',
    'set_stop', 'set_target', 'set_trail', 'position', 'equity', 'cash',
    'bars_held', 'unrealised_pct', 'entry_price', 'bars_available',
])

/**
 * Tokenise one line for display.
 *
 * Deliberately not a parser. It runs on every keystroke over the whole
 * document, so it is a single left-to-right pass with no backtracking; the
 * only cross-line state is whether a triple-quoted string is open, which is
 * threaded through by the caller.
 */
function tokenise(line, inTriple) {
    const out = []
    let i = 0
    let triple = inTriple

    const push = (text, cls) => {
        if (text) out.push({ text, cls })
    }

    if (triple) {
        const end = line.indexOf(triple)
        if (end === -1) {
            push(line, 'tok-string')
            return { tokens: out, triple }
        }
        push(line.slice(0, end + 3), 'tok-string')
        i = end + 3
        triple = null
    }

    while (i < line.length) {
        const rest = line.slice(i)

        if (rest[0] === '#') {
            push(rest, 'tok-comment')
            break
        }

        const tripleMatch = rest.match(/^("""|''')/)
        if (tripleMatch) {
            const quote = tripleMatch[1]
            const end = rest.indexOf(quote, 3)
            if (end === -1) {
                push(rest, 'tok-string')
                triple = quote
                break
            }
            push(rest.slice(0, end + 3), 'tok-string')
            i += end + 3
            continue
        }

        const stringMatch = rest.match(/^(f?)(["'])(?:\\.|(?!\2)[^\\])*\2?/)
        if (stringMatch) {
            push(stringMatch[0], 'tok-string')
            i += stringMatch[0].length
            continue
        }

        const numberMatch = rest.match(/^\d+\.?\d*(e[+-]?\d+)?/i)
        if (numberMatch) {
            push(numberMatch[0], 'tok-number')
            i += numberMatch[0].length
            continue
        }

        const wordMatch = rest.match(/^[A-Za-z_][A-Za-z0-9_]*/)
        if (wordMatch) {
            const word = wordMatch[0]
            const before = line.slice(0, i)
            const after = line.slice(i + word.length)
            let cls = 'tok-plain'

            if (KEYWORDS.has(word)) cls = 'tok-keyword'
            else if (/\.\s*$/.test(before) && CTX_METHODS.has(word)) cls = 'tok-method'
            else if (BUILTINS.has(word)) cls = 'tok-builtin'
            else if (/^\s*\(/.test(after)) cls = 'tok-call'
            else if (/^[A-Z][A-Z0-9_]*$/.test(word)) cls = 'tok-const'

            push(word, cls)
            i += word.length
            continue
        }

        const operatorMatch = rest.match(/^[+\-*/%=<>!&|^~@]+/)
        if (operatorMatch) {
            push(operatorMatch[0], 'tok-operator')
            i += operatorMatch[0].length
            continue
        }

        push(rest[0], 'tok-plain')
        i += 1
    }

    return { tokens: out, triple }
}

function highlight(source) {
    const lines = source.split('\n')
    let triple = null
    return lines.map((line) => {
        const { tokens, triple: next } = tokenise(line, triple)
        triple = next
        return tokens
    })
}

export default function CodeEditor({
    value,
    onChange,
    errorLine = null,
    warnLines = [],
    onRun,
    onSave,
    readOnly = false,
    minHeight = 380,
    className = '',
}) {
    const textarea = useRef(null)
    const pre = useRef(null)
    const gutter = useRef(null)
    const [caretLine, setCaretLine] = useState(1)

    const painted = useMemo(() => highlight(value || ''), [value])
    const lineCount = painted.length

    // One scroll position, three layers. Driven from the textarea because that
    // is the only one the user can actually scroll.
    const syncScroll = useCallback(() => {
        const el = textarea.current
        if (!el) return
        if (pre.current) {
            pre.current.scrollTop = el.scrollTop
            pre.current.scrollLeft = el.scrollLeft
        }
        if (gutter.current) gutter.current.scrollTop = el.scrollTop
    }, [])

    const trackCaret = useCallback(() => {
        const el = textarea.current
        if (!el) return
        const upto = el.value.slice(0, el.selectionStart)
        setCaretLine(upto.split('\n').length)
    }, [])

    // Scroll an error into view when one arrives — an error on line 90 of a
    // scrolled-away file is otherwise reported and invisible.
    useEffect(() => {
        if (!errorLine || !textarea.current) return
        const lineHeight = 13 * 1.55
        const target = Math.max(0, (errorLine - 6) * lineHeight)
        textarea.current.scrollTop = target
        syncScroll()
    }, [errorLine, syncScroll])

    const replaceSelection = useCallback(
        (insert, selectionStart, selectionEnd, caretOffset = null) => {
            const el = textarea.current
            if (!el) return
            const next =
                el.value.slice(0, selectionStart) + insert + el.value.slice(selectionEnd)
            onChange(next)
            const caret = caretOffset ?? selectionStart + insert.length
            requestAnimationFrame(() => {
                el.selectionStart = el.selectionEnd = caret
                trackCaret()
            })
        },
        [onChange, trackCaret]
    )

    const handleKeyDown = useCallback(
        (event) => {
            const el = textarea.current
            if (!el) return
            const { selectionStart: start, selectionEnd: end, value: text } = el

            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                event.preventDefault()
                onRun?.()
                return
            }
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
                event.preventDefault()
                onSave?.()
                return
            }
            if (readOnly) return

            // Tab indents; with a selection or Shift held it works on whole
            // lines, because that is what Tab means in every editor people
            // arrive here from.
            if (event.key === 'Tab') {
                event.preventDefault()
                const lineStart = text.lastIndexOf('\n', start - 1) + 1

                if (start !== end || event.shiftKey) {
                    const lineEnd = text.indexOf('\n', end) === -1 ? text.length : text.indexOf('\n', end)
                    const block = text.slice(lineStart, lineEnd)
                    const shifted = event.shiftKey
                        ? block.replace(/^ {1,4}/gm, '')
                        : block.replace(/^/gm, '    ')
                    const next = text.slice(0, lineStart) + shifted + text.slice(lineEnd)
                    onChange(next)
                    requestAnimationFrame(() => {
                        el.selectionStart = lineStart
                        el.selectionEnd = lineStart + shifted.length
                    })
                    return
                }
                replaceSelection('    ', start, end)
                return
            }

            if (event.key === 'Enter') {
                event.preventDefault()
                const lineStart = text.lastIndexOf('\n', start - 1) + 1
                const currentLine = text.slice(lineStart, start)
                const indent = (currentLine.match(/^\s*/) || [''])[0]
                // A trailing colon opens a block, so the next line belongs one
                // level deeper. Getting this wrong is the single most annoying
                // thing a Python editor can do.
                const deeper = /:\s*$/.test(currentLine) ? '    ' : ''
                replaceSelection(`\n${indent}${deeper}`, start, end)
                return
            }

            // Backspace at an indent boundary removes the whole level.
            if (event.key === 'Backspace' && start === end) {
                const lineStart = text.lastIndexOf('\n', start - 1) + 1
                const before = text.slice(lineStart, start)
                if (before.length >= 4 && /^ +$/.test(before) && before.length % 4 === 0) {
                    event.preventDefault()
                    replaceSelection('', start - 4, end)
                    return
                }
            }

            const pairs = { '(': ')', '[': ']', '{': '}' }
            if (pairs[event.key] && start === end) {
                event.preventDefault()
                replaceSelection(event.key + pairs[event.key], start, end, start + 1)
                return
            }

            // Typing the closing half of a pair the editor inserted should step
            // over it rather than doubling it.
            if ([')', ']', '}'].includes(event.key) && text[start] === event.key && start === end) {
                event.preventDefault()
                requestAnimationFrame(() => {
                    el.selectionStart = el.selectionEnd = start + 1
                })
            }
        },
        [onChange, onRun, onSave, readOnly, replaceSelection]
    )

    const warnSet = useMemo(() => new Set(warnLines), [warnLines])

    return (
        <div
            className={`code-editor material-raised relative overflow-hidden rounded-lg ${className}`}
            style={{ minHeight }}
        >
            <div className="flex h-full">
                {/* Gutter */}
                <div
                    ref={gutter}
                    className={`${SHARED} select-none overflow-hidden border-r border-dark-700/60 bg-dark-950/40 py-3 text-right`}
                    style={{ minWidth: 46, lineHeight: METRICS.lineHeight }}
                    aria-hidden="true"
                >
                    {painted.map((_, index) => {
                        const line = index + 1
                        const isError = errorLine === line
                        const isWarn = warnSet.has(line)
                        return (
                            <div
                                key={line}
                                className={`px-2 ${
                                    isError
                                        ? 'bg-danger-500/20 font-semibold text-danger-300'
                                        : isWarn
                                          ? 'bg-alert-500/15 text-alert-300'
                                          : line === caretLine
                                            ? 'text-primary-300'
                                            : 'text-dark-600'
                                }`}
                            >
                                {line}
                            </div>
                        )
                    })}
                </div>

                {/* Text + paint */}
                <div className="relative min-w-0 flex-1">
                    <pre
                        ref={pre}
                        className={`${SHARED} pointer-events-none absolute inset-0 overflow-hidden`}
                        style={{ ...METRICS, margin: 0 }}
                        aria-hidden="true"
                    >
                        {painted.map((tokens, index) => (
                            <div
                                key={index}
                                className={
                                    errorLine === index + 1
                                        ? 'bg-danger-500/10'
                                        : warnSet.has(index + 1)
                                          ? 'bg-alert-500/[0.07]'
                                          : ''
                                }
                            >
                                {tokens.length === 0 ? (
                                    '​'
                                ) : (
                                    tokens.map((token, tokenIndex) => (
                                        <span key={tokenIndex} className={token.cls}>
                                            {token.text}
                                        </span>
                                    ))
                                )}
                            </div>
                        ))}
                    </pre>

                    <textarea
                        ref={textarea}
                        value={value}
                        onChange={(event) => onChange(event.target.value)}
                        onScroll={syncScroll}
                        onKeyDown={handleKeyDown}
                        onKeyUp={trackCaret}
                        onClick={trackCaret}
                        readOnly={readOnly}
                        spellCheck={false}
                        autoCapitalize="off"
                        autoCorrect="off"
                        autoComplete="off"
                        wrap="off"
                        className={`${SHARED} absolute inset-0 h-full w-full resize-none bg-transparent text-transparent caret-primary-300 outline-none`}
                        style={{ ...METRICS }}
                    />
                </div>
            </div>

            {/* Status strip */}
            <div className="pointer-events-none absolute bottom-0 right-0 flex items-center gap-3 rounded-tl-md border-l border-t border-dark-700/60 bg-dark-950/85 px-2.5 py-1 font-mono text-[10px] text-dark-500">
                <span>
                    Ln {caretLine} · {lineCount} lines
                </span>
                <span className="text-dark-700">|</span>
                <span>⌘↵ run</span>
            </div>
        </div>
    )
}

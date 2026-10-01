import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
    AlertTriangle, BookOpen, Check, ChevronDown, Copy, FlaskConical, GitBranch,
    Layers, Play, Radio, Save, Settings2, Trash2, TrendingUp, Zap,
} from 'lucide-react'
import CodeEditor from '../components/CodeEditor'
import { HudPanel, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import {
    ComparisonTable, Concerns, DrawdownCurve, EquityCurve, MetricGrid,
    MonthlyReturns, SweepPanel, TradeTable, WalkForwardPanel,
} from '../components/studio/Results'
import { useToast } from '../store/toast'
import { strategyApi } from '../services/api'

/**
 * The Strategy Studio.
 *
 * Three tests, presented in the order of how much they are worth, which is
 * the opposite of the order people run them in:
 *
 *   Backtest      one setting on all the history. Descriptive, not predictive.
 *   Sweep         every setting. Finds a region, and tells you whether that
 *                 region is a plateau or a lucky spike.
 *   Walk-forward  the only one that tests on data the search never saw.
 *
 * The tab order reflects that, and the walk-forward tab is the one that
 * carries a verdict in words. A studio that made the flattering test the
 * default would be teaching the wrong lesson very efficiently.
 */

const PERIODS = ['1y', '2y', '5y', '10y', 'max']

const TABS = [
    { key: 'backtest', label: 'Backtest', icon: Play,
      hint: 'One setting, all the history. Describes the past.' },
    { key: 'sweep', label: 'Sweep', icon: Layers,
      hint: 'Every setting, ranked, with a stability score per cell.' },
    { key: 'walkforward', label: 'Walk-forward', icon: GitBranch,
      hint: 'Out-of-sample. The only test that predicts anything.' },
    { key: 'compare', label: 'Compare all', icon: TrendingUp,
      hint: 'Every built-in on this symbol, against buy-and-hold.' },
]

export default function Studio() {
    const toast = useToast()
    const [searchParams, setSearchParams] = useSearchParams()

    // ---- source of truth -------------------------------------------------
    const [source, setSource] = useState('')
    const [origin, setOrigin] = useState({ kind: 'template', key: null, id: null, name: 'Untitled' })
    const [dirty, setDirty] = useState(false)

    // ---- catalogue -------------------------------------------------------
    const [builtins, setBuiltins] = useState([])
    const [template, setTemplate] = useState('')
    const [objectives, setObjectives] = useState([])
    const [saved, setSaved] = useState([])
    const [reference, setReference] = useState(null)

    // ---- run configuration ----------------------------------------------
    const [symbol, setSymbol] = useState('RELIANCE')
    const [period, setPeriod] = useState('5y')
    const [params, setParams] = useState({})
    const [paramSpecs, setParamSpecs] = useState([])
    const [capital, setCapital] = useState(100000)
    const [riskPerTrade, setRiskPerTrade] = useState(2)
    const [objective, setObjective] = useState('robust')
    const [folds, setFolds] = useState(5)
    const [showConfig, setShowConfig] = useState(false)

    // ---- state -----------------------------------------------------------
    const [tab, setTab] = useState('backtest')
    const [validation, setValidation] = useState({ valid: true, errors: [] })
    const [busy, setBusy] = useState(null)
    const [error, setError] = useState(null)
    const [results, setResults] = useState({})
    const [showHelp, setShowHelp] = useState(false)

    const validateTimer = useRef(null)

    // ---- load the catalogue ---------------------------------------------
    useEffect(() => {
        let alive = true
        Promise.allSettled([
            strategyApi.library(),
            strategyApi.list(),
            strategyApi.reference(),
        ]).then(([lib, mine, ref]) => {
            if (!alive) return
            if (lib.status === 'fulfilled') {
                setBuiltins(lib.value.data.strategies || [])
                setTemplate(lib.value.data.template || '')
                setObjectives(lib.value.data.objectives || [])
                if (!source) {
                    const start = (lib.value.data.strategies || []).find((s) => s.key === 'adx_trend')
                    if (start) {
                        setSource(start.source)
                        setOrigin({ kind: 'builtin', key: start.key, id: null, name: start.name })
                        setParamSpecs(start.params || [])
                        setParams(Object.fromEntries((start.params || []).map((p) => [p.name, p.default])))
                    } else {
                        setSource(lib.value.data.template || '')
                    }
                }
            }
            if (mine.status === 'fulfilled') setSaved(mine.value.data.strategies || [])
            if (ref.status === 'fulfilled') setReference(ref.value.data)
        })
        return () => {
            alive = false
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    // ---- deep links from the command palette ----------------------------
    //
    // Consumed once and then stripped from the URL, so a reload does not
    // re-open a strategy the user has since navigated away from inside the
    // studio — the URL describes how you arrived, not where you are now.
    useEffect(() => {
        const wanted = searchParams.get('symbol')
        const strategyParam = searchParams.get('strategy')
        if (!wanted && !strategyParam) return

        if (wanted) setSymbol(wanted.toUpperCase())
        if (strategyParam) loadSaved(Number(strategyParam))
        setSearchParams({}, { replace: true })
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [searchParams])

    // ---- validate as you type -------------------------------------------
    //
    // Debounced, and failures are rendered inline rather than thrown: someone
    // halfway through typing `def on_b` is not making a mistake yet.
    useEffect(() => {
        if (!source) return
        clearTimeout(validateTimer.current)
        validateTimer.current = setTimeout(async () => {
            try {
                const { data } = await strategyApi.validate(source)
                setValidation(data)
                if (data.valid && data.strategy?.params) {
                    setParamSpecs(data.strategy.params)
                    setParams((previous) => {
                        const next = {}
                        for (const spec of data.strategy.params) {
                            next[spec.name] = previous[spec.name] ?? spec.default
                        }
                        return next
                    })
                }
            } catch {
                /* a transient network failure must not clear a valid state */
            }
        }, 450)
        return () => clearTimeout(validateTimer.current)
    }, [source])

    const errorLine = validation.errors?.[0]?.line ?? null

    // ---- running ---------------------------------------------------------

    const basePayload = useCallback(
        () => ({
            symbol: symbol.trim().toUpperCase(),
            source,
            params,
            period,
            config: {
                initial_capital: Number(capital) || 100000,
                risk_per_trade: (Number(riskPerTrade) || 2) / 100,
            },
        }),
        [symbol, source, params, period, capital, riskPerTrade]
    )

    const run = useCallback(
        async (kind) => {
            if (!symbol.trim()) {
                toast.warn('Enter a symbol first')
                return
            }
            if (!validation.valid && kind !== 'compare') {
                toast.error(validation.errors?.[0]?.message || 'Fix the strategy first')
                return
            }

            setBusy(kind)
            setError(null)
            try {
                const payload = basePayload()
                let response
                if (kind === 'backtest') {
                    response = await strategyApi.backtest(payload)
                } else if (kind === 'sweep') {
                    response = await strategyApi.sweep({ ...payload, objective })
                } else if (kind === 'walkforward') {
                    response = await strategyApi.walkforward({ ...payload, objective, folds })
                } else {
                    response = await strategyApi.compare(payload)
                }

                setResults((previous) => ({ ...previous, [kind]: response.data }))
                setTab(kind)

                if (response.data.error) {
                    toast.error(response.data.error)
                } else if (kind === 'walkforward') {
                    const efficiency = response.data.efficiency
                    if (efficiency !== null && efficiency !== undefined && efficiency < 0.3) {
                        toast.warn('Out-of-sample collapsed — this strategy was fitted')
                    } else {
                        toast.ok('Walk-forward complete')
                    }
                }
            } catch (exception) {
                const detail =
                    exception?.response?.data?.detail || exception.message || 'Run failed'
                setError(detail)
                toast.error(detail)
            } finally {
                setBusy(null)
            }
        },
        [symbol, validation, basePayload, objective, folds, toast]
    )

    // ---- library actions -------------------------------------------------

    const loadBuiltin = useCallback(
        (key) => {
            const entry = builtins.find((b) => b.key === key)
            if (!entry) return
            if (dirty && !window.confirm('Discard unsaved changes to the current strategy?')) return
            setSource(entry.source)
            setOrigin({ kind: 'builtin', key: entry.key, id: null, name: entry.name })
            setParamSpecs(entry.params || [])
            setParams(Object.fromEntries((entry.params || []).map((p) => [p.name, p.default])))
            setResults({})
            setDirty(false)
        },
        [builtins, dirty]
    )

    const loadSaved = useCallback(
        async (id) => {
            if (dirty && !window.confirm('Discard unsaved changes to the current strategy?')) return
            try {
                const { data } = await strategyApi.get(id)
                setSource(data.source)
                setOrigin({ kind: 'saved', key: null, id: data.id, name: data.name })
                setParams(data.params || {})
                setResults({})
                setDirty(false)
            } catch (exception) {
                toast.error(exception?.response?.data?.detail || 'Could not open that strategy')
            }
        },
        [dirty, toast]
    )

    const save = useCallback(async () => {
        if (!validation.valid) {
            toast.error('Fix the strategy before saving')
            return
        }
        try {
            if (origin.kind === 'saved' && origin.id) {
                await strategyApi.update(origin.id, { source, params })
                toast.ok(`Saved ${origin.name}`)
            } else {
                const name = window.prompt('Name this strategy', origin.name || 'My strategy')
                if (!name) return
                const { data } = await strategyApi.save({
                    name,
                    source,
                    params,
                    forked_from: origin.kind === 'builtin' ? origin.key || '' : '',
                })
                setOrigin({ kind: 'saved', key: null, id: data.id, name: data.name })
                toast.ok(`Saved ${data.name}`)
            }
            setDirty(false)
            const { data } = await strategyApi.list()
            setSaved(data.strategies || [])
        } catch (exception) {
            toast.error(exception?.response?.data?.detail || 'Save failed')
        }
    }, [validation, origin, source, params, toast])

    const remove = useCallback(
        async (id, name) => {
            if (!window.confirm(`Delete "${name}"? Its version history goes too.`)) return
            try {
                await strategyApi.remove(id)
                setSaved((previous) => previous.filter((s) => s.id !== id))
                if (origin.id === id) setOrigin({ ...origin, kind: 'template', id: null })
                toast.ok(`Deleted ${name}`)
            } catch (exception) {
                toast.error(exception?.response?.data?.detail || 'Delete failed')
            }
        },
        [origin, toast]
    )

    const applyParams = useCallback(
        (next) => {
            setParams((previous) => ({ ...previous, ...next }))
            toast.ok('Parameters applied — re-run the backtest to see them')
        },
        [toast]
    )

    const current = results[tab]
    const grouped = useMemo(() => {
        const out = {}
        for (const entry of builtins) {
            ;(out[entry.category] ||= []).push(entry)
        }
        return out
    }, [builtins])

    return (
        <div className="flex h-full min-h-0 flex-col gap-4">
            {/* ---- Header ---- */}
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                    <FlaskConical className="h-5 w-5 shrink-0 text-primary-400" />
                    <div className="min-w-0">
                        <h1 className="truncate font-mono text-sm uppercase tracking-[0.25em] text-primary-300">
                            Strategy Studio
                        </h1>
                        <p className="truncate text-[11px] text-dark-500">
                            {origin.name}
                            {dirty && <span className="ml-1.5 text-alert-400">• unsaved</span>}
                            {origin.kind === 'builtin' && (
                                <span className="ml-1.5 text-dark-600">— built-in, edit freely</span>
                            )}
                        </p>
                    </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    <input
                        value={symbol}
                        onChange={(event) => setSymbol(event.target.value.toUpperCase())}
                        placeholder="SYMBOL"
                        className="input-field !w-28 !py-1.5 uppercase"
                    />
                    <select
                        value={period}
                        onChange={(event) => setPeriod(event.target.value)}
                        className="input-field !w-[86px] !py-1.5"
                    >
                        {PERIODS.map((option) => (
                            <option key={option} value={option}>
                                {option}
                            </option>
                        ))}
                    </select>
                    <button
                        onClick={() => setShowConfig((value) => !value)}
                        className="btn-ghost !px-2.5 !py-1.5"
                        title="Capital, risk and objective"
                    >
                        <Settings2 className="h-4 w-4" />
                    </button>
                    <button onClick={save} className="btn-ghost !py-1.5" title="⌘S">
                        <Save className="h-3.5 w-3.5" /> Save
                    </button>
                    <button
                        onClick={() => run('backtest')}
                        disabled={busy !== null}
                        className="btn-primary !py-1.5"
                        title="⌘↵"
                    >
                        {busy === 'backtest' ? (
                            <Zap className="h-3.5 w-3.5 animate-pulse" />
                        ) : (
                            <Play className="h-3.5 w-3.5" />
                        )}
                        Run
                    </button>
                </div>
            </div>

            {showConfig && (
                <HudPanel corners={false} className="shrink-0">
                    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                        <Field label="Starting capital" hint="₹">
                            <input
                                type="number"
                                value={capital}
                                onChange={(event) => setCapital(event.target.value)}
                                className="input-field !py-1.5"
                            />
                        </Field>
                        <Field label="Risk per trade" hint="% of equity when a stop is set">
                            <input
                                type="number"
                                step="0.5"
                                value={riskPerTrade}
                                onChange={(event) => setRiskPerTrade(event.target.value)}
                                className="input-field !py-1.5"
                            />
                        </Field>
                        <Field label="Sweep objective" hint="What the optimiser maximises">
                            <select
                                value={objective}
                                onChange={(event) => setObjective(event.target.value)}
                                className="input-field !py-1.5"
                            >
                                {objectives.map((option) => (
                                    <option key={option.key} value={option.key}>
                                        {option.key}
                                    </option>
                                ))}
                            </select>
                        </Field>
                        <Field label="Walk-forward folds" hint="More folds, shorter windows">
                            <input
                                type="number"
                                min="2"
                                max="12"
                                value={folds}
                                onChange={(event) => setFolds(Number(event.target.value))}
                                className="input-field !py-1.5"
                            />
                        </Field>
                    </div>
                    {objectives.find((o) => o.key === objective) && (
                        <p className="mt-3 text-[11px] leading-relaxed text-dark-500">
                            {objectives.find((o) => o.key === objective).label}
                        </p>
                    )}
                </HudPanel>
            )}

            {/* ---- Body ---- */}
            <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-[260px_minmax(0,1fr)]">
                {/* Library rail */}
                <aside className="hidden min-h-0 flex-col gap-3 overflow-y-auto xl:flex">
                    <HudPanel title="Your strategies" corners={false} padded={false}>
                        {saved.length === 0 ? (
                            <p className="px-4 py-4 text-[11px] leading-relaxed text-dark-500">
                                Nothing saved yet. Fork a built-in below, change it, and hit Save.
                            </p>
                        ) : (
                            <div className="p-1.5">
                                {saved.map((entry) => (
                                    <div
                                        key={entry.id}
                                        className={`group flex items-center gap-1 rounded px-2 py-1.5 transition-colors ${
                                            origin.id === entry.id
                                                ? 'bg-primary-500/10 text-primary-200'
                                                : 'text-dark-300 hover:bg-dark-800/50'
                                        }`}
                                    >
                                        <button
                                            onClick={() => loadSaved(entry.id)}
                                            className="min-w-0 flex-1 truncate text-left text-xs"
                                        >
                                            {entry.name}
                                            {entry.deployed && (
                                                <Radio className="ml-1.5 inline h-2.5 w-2.5 text-success-400" />
                                            )}
                                        </button>
                                        <button
                                            onClick={() => remove(entry.id, entry.name)}
                                            className="opacity-0 transition-opacity group-hover:opacity-100"
                                            title="Delete"
                                        >
                                            <Trash2 className="h-3 w-3 text-dark-500 hover:text-danger-400" />
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </HudPanel>

                    <HudPanel title="Built-in" corners={false} padded={false}>
                        <div className="p-1.5">
                            <button
                                onClick={() => {
                                    if (dirty && !window.confirm('Discard unsaved changes?')) return
                                    setSource(template)
                                    setOrigin({ kind: 'template', key: null, id: null, name: 'Untitled' })
                                    setResults({})
                                    setDirty(false)
                                }}
                                className="mb-1 w-full rounded border border-dashed border-dark-600/70 px-2 py-1.5 text-left text-xs text-dark-400 transition-colors hover:border-primary-400/50 hover:text-primary-300"
                            >
                                + Blank strategy
                            </button>
                            {Object.entries(grouped).map(([category, entries]) => (
                                <div key={category} className="mt-2">
                                    <div className="px-2 py-1 font-mono text-[9px] uppercase tracking-[0.2em] text-dark-600">
                                        {category}
                                    </div>
                                    {entries.map((entry) => (
                                        <button
                                            key={entry.key}
                                            onClick={() => loadBuiltin(entry.key)}
                                            title={entry.description}
                                            className={`block w-full truncate rounded px-2 py-1.5 text-left text-xs transition-colors ${
                                                origin.key === entry.key
                                                    ? 'bg-primary-500/10 text-primary-200'
                                                    : 'text-dark-300 hover:bg-dark-800/50'
                                            }`}
                                        >
                                            {entry.name}
                                        </button>
                                    ))}
                                </div>
                            ))}
                        </div>
                    </HudPanel>
                </aside>

                {/* Editor + results */}
                <div className="flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
                    <HudPanel
                        padded={false}
                        corners={false}
                        title="Strategy"
                        right={
                            <div className="flex items-center gap-2">
                                <button
                                    onClick={() => setShowHelp((value) => !value)}
                                    className="hud-label transition-colors hover:text-primary-300"
                                >
                                    <BookOpen className="mr-1 inline h-3 w-3" />
                                    API
                                </button>
                                <button
                                    onClick={() => {
                                        navigator.clipboard?.writeText(source)
                                        toast.ok('Copied')
                                    }}
                                    className="hud-label transition-colors hover:text-primary-300"
                                >
                                    <Copy className="mr-1 inline h-3 w-3" />
                                    Copy
                                </button>
                            </div>
                        }
                    >
                        <CodeEditor
                            value={source}
                            onChange={(next) => {
                                setSource(next)
                                setDirty(true)
                            }}
                            errorLine={errorLine}
                            onRun={() => run('backtest')}
                            onSave={save}
                            minHeight={340}
                        />

                        {!validation.valid && validation.errors?.length > 0 && (
                            <div className="border-t border-danger-400/25 bg-danger-500/10 px-4 py-2.5">
                                {validation.errors.map((entry, index) => (
                                    <p key={index} className="flex items-start gap-2 font-mono text-[11px] text-danger-300">
                                        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                                        {entry.message}
                                    </p>
                                ))}
                            </div>
                        )}
                        {validation.valid && (
                            <div className="flex items-center gap-2 border-t border-success-400/15 px-4 py-2 font-mono text-[11px] text-success-400/80">
                                <Check className="h-3 w-3" />
                                Valid
                                {validation.tunable?.length > 0 && (
                                    <span className="text-dark-500">
                                        · {validation.tunable.length} tunable parameter
                                        {validation.tunable.length === 1 ? '' : 's'}
                                    </span>
                                )}
                            </div>
                        )}
                    </HudPanel>

                    {showHelp && reference && <ApiReference reference={reference} />}

                    {paramSpecs.length > 0 && (
                        <HudPanel title="Parameters" corners={false}>
                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                {paramSpecs.map((spec) => (
                                    <ParamControl
                                        key={spec.name}
                                        spec={spec}
                                        value={params[spec.name] ?? spec.default}
                                        onChange={(value) =>
                                            setParams((previous) => ({ ...previous, [spec.name]: value }))
                                        }
                                    />
                                ))}
                            </div>
                        </HudPanel>
                    )}

                    {/* ---- Test tabs ---- */}
                    <HudPanel padded={false} corners={false}>
                        <div className="flex flex-wrap items-center gap-1 border-b border-primary-400/10 p-2">
                            {TABS.map((entry) => {
                                const active = tab === entry.key
                                return (
                                    <button
                                        key={entry.key}
                                        onClick={() => setTab(entry.key)}
                                        title={entry.hint}
                                        className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-all ${
                                            active
                                                ? 'bg-primary-500/12 text-primary-200'
                                                : 'text-dark-400 hover:text-dark-100'
                                        }`}
                                    >
                                        <entry.icon className="h-3.5 w-3.5" />
                                        {entry.label}
                                        {results[entry.key] && !active && (
                                            <span className="h-1 w-1 rounded-full bg-primary-400/70" />
                                        )}
                                    </button>
                                )
                            })}
                            <div className="flex-1" />
                            <button
                                onClick={() => run(tab)}
                                disabled={busy !== null}
                                className="btn-primary !px-3 !py-1.5 !text-[10px]"
                            >
                                {busy === tab ? (
                                    <>
                                        <Zap className="h-3 w-3 animate-pulse" /> Running
                                    </>
                                ) : (
                                    <>
                                        <Play className="h-3 w-3" /> Run {TABS.find((t) => t.key === tab)?.label}
                                    </>
                                )}
                            </button>
                        </div>

                        <div className="p-4">
                            <p className="mb-4 text-[11px] leading-relaxed text-dark-500">
                                {TABS.find((t) => t.key === tab)?.hint}
                            </p>

                            <ErrorNote error={error} onRetry={() => run(tab)} />

                            {busy === tab && <Loading rows={4} label={`Running ${tab}`} />}

                            {!busy && !current && !error && (
                                <Empty
                                    icon={TABS.find((t) => t.key === tab)?.icon}
                                    title="Not run yet"
                                    hint={
                                        tab === 'walkforward'
                                            ? 'This is the slow one — several hundred backtests. It is also the only result worth acting on.'
                                            : 'Set a symbol above and run.'
                                    }
                                />
                            )}

                            {!busy && current && (
                                <>
                                    {tab === 'backtest' && <BacktestView result={current} />}
                                    {tab === 'sweep' && <SweepPanel result={current} onApply={applyParams} />}
                                    {tab === 'walkforward' && <WalkForwardPanel result={current} />}
                                    {tab === 'compare' && (
                                        <ComparisonTable result={current} onPick={loadBuiltin} />
                                    )}
                                </>
                            )}
                        </div>
                    </HudPanel>
                </div>
            </div>
        </div>
    )
}

/* ------------------------------------------------------------------ */

function BacktestView({ result }) {
    if (result.error) {
        return (
            <div className="rounded-lg border border-danger-400/30 bg-danger-500/10 p-4">
                <p className="font-mono text-xs text-danger-300">{result.error}</p>
                {result.error_line && (
                    <p className="mt-1 text-[11px] text-dark-500">
                        Strategy line {result.error_line}
                    </p>
                )}
            </div>
        )
    }

    return (
        <div className="space-y-5">
            <Concerns items={result.concerns} />

            <div>
                <div className="mb-2 flex items-center justify-between">
                    <span className="hud-label">Equity, indexed to 100</span>
                    <span className="font-mono text-[10px] text-dark-500">
                        {result.data?.start} → {result.data?.end} · {result.data?.bars} bars ·
                        dashed line is buy &amp; hold
                    </span>
                </div>
                <EquityCurve curve={result.curve} />
                <DrawdownCurve curve={result.curve} />
            </div>

            <MetricGrid metrics={result.metrics} />

            {result.metrics?.monthly_returns?.length > 0 && (
                <div>
                    <div className="hud-label mb-2">Month by month</div>
                    <MonthlyReturns months={result.metrics.monthly_returns} />
                </div>
            )}

            <div>
                <div className="hud-label mb-2">
                    Every trade · {result.trades?.length || 0}
                </div>
                <TradeTable trades={result.trades} />
            </div>

            {result.logs?.length > 0 && (
                <div>
                    <div className="hud-label mb-2">Strategy log</div>
                    <pre className="max-h-48 overflow-auto rounded border border-dark-700/60 bg-dark-950/60 p-3 font-mono text-[10px] leading-relaxed text-dark-400">
                        {result.logs.join('\n')}
                    </pre>
                </div>
            )}
        </div>
    )
}

function ParamControl({ spec, value, onChange }) {
    const numeric = typeof spec.default === 'number'

    if (!numeric || !spec.tunable) {
        return (
            <Field label={spec.label || spec.name} hint={spec.tunable ? '' : 'fixed'}>
                <input
                    value={value ?? ''}
                    onChange={(event) =>
                        onChange(numeric ? Number(event.target.value) : event.target.value)
                    }
                    className="input-field !py-1.5"
                />
            </Field>
        )
    }

    return (
        <Field label={spec.label || spec.name} hint={`${spec.low}–${spec.high}`}>
            <div className="flex items-center gap-2">
                <input
                    type="range"
                    min={spec.low}
                    max={spec.high}
                    step={spec.step || 1}
                    value={value}
                    onChange={(event) => onChange(Number(event.target.value))}
                    className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-dark-700 accent-primary-400"
                />
                <input
                    type="number"
                    value={value}
                    step={spec.step || 1}
                    onChange={(event) => onChange(Number(event.target.value))}
                    className="input-field !w-[72px] !px-2 !py-1 !text-xs"
                />
            </div>
        </Field>
    )
}

function Field({ label, hint, children }) {
    return (
        <label className="block">
            <div className="mb-1 flex items-baseline justify-between gap-2">
                <span className="hud-label">{label}</span>
                {hint && <span className="truncate font-mono text-[9px] text-dark-600">{hint}</span>}
            </div>
            {children}
        </label>
    )
}

function ApiReference({ reference }) {
    const [open, setOpen] = useState(reference.context?.[0]?.group || null)

    return (
        <HudPanel title="Strategy API" corners={false} padded={false}>
            <div className="border-b border-primary-400/10 p-4">
                <div className="hud-label mb-2">Rules the engine enforces</div>
                <ul className="space-y-1.5">
                    {reference.rules?.map((rule, index) => (
                        <li key={index} className="flex gap-2 text-[11px] leading-relaxed text-dark-300">
                            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-alert-400/70" />
                            <span>{rule}</span>
                        </li>
                    ))}
                </ul>
            </div>

            {reference.context?.map((group) => (
                <div key={group.group} className="border-b border-dark-800/60 last:border-0">
                    <button
                        onClick={() => setOpen(open === group.group ? null : group.group)}
                        className="flex w-full items-center justify-between px-4 py-2.5 text-left transition-colors hover:bg-dark-800/40"
                    >
                        <span className="hud-label">{group.group}</span>
                        <ChevronDown
                            className={`h-3.5 w-3.5 text-dark-500 transition-transform ${
                                open === group.group ? 'rotate-180' : ''
                            }`}
                        />
                    </button>
                    {open === group.group && (
                        <div className="space-y-1 px-4 pb-3">
                            {group.items.map((item) => (
                                <div key={item.call} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                                    <code className="font-mono text-[11px] text-primary-300">{item.call}</code>
                                    {item.returns && (
                                        <span className="font-mono text-[10px] text-dark-600">
                                            → {item.returns}
                                        </span>
                                    )}
                                    {item.note && (
                                        <span className="text-[11px] text-dark-500">{item.note}</span>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            ))}
        </HudPanel>
    )
}

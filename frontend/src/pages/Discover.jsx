import { useCallback, useEffect, useState } from 'react'
import { ArrowRight, Check, Compass, ShieldCheck, Sparkles, X } from 'lucide-react'
import { HudPanel, Stat, Meter, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import { discoverApi } from '../services/api'

/**
 * Discovery — find what you're missing, and check whether it suits you.
 *
 * Ordered the way the decision should actually be made: profile first, then
 * the gaps that profile implies, then instruments to fill them.
 */

const VERDICT_STYLE = {
    suitable: 'badge-success',
    suitable_with_caution: 'badge-alert',
    unsuitable: 'badge-danger',
}

export default function Discover() {
    const [profile, setProfile] = useState(null)
    const [gaps, setGaps] = useState(null)
    const [recommended, setRecommended] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [quizOpen, setQuizOpen] = useState(false)
    const [checking, setChecking] = useState(null)
    const [checkResult, setCheckResult] = useState(null)

    const load = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const [p, g, r] = await Promise.all([
                discoverApi.profile(),
                discoverApi.gaps(),
                discoverApi.recommended(null, 12),
            ])
            setProfile(p.data)
            setGaps(g.data)
            setRecommended(r.data.recommendations || [])
        } catch (err) {
            setError(err?.response?.data?.detail || err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        load()
    }, [load])

    const checkSuitability = async (symbol) => {
        setChecking(symbol)
        setCheckResult(null)
        try {
            const { data } = await discoverApi.suitability(symbol)
            setCheckResult(data)
        } catch (err) {
            setCheckResult({ error: err?.response?.data?.detail || err.message })
        } finally {
            setChecking(null)
        }
    }

    if (loading && !profile) return <Loading rows={5} label="Building your profile" />

    return (
        <div className="space-y-5">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="font-mono text-lg uppercase tracking-[0.3em] text-primary-300 text-glow">
                        Discover
                    </h1>
                    <p className="mt-1 text-xs text-dark-400">
                        Beyond equities — REITs, InvITs, bonds, G-Secs and ETFs, matched to you.
                    </p>
                </div>
                <button onClick={() => setQuizOpen(true)} className="btn-primary">
                    <ShieldCheck className="h-3.5 w-3.5" />
                    {profile?.is_default ? 'Take risk assessment' : 'Retake assessment'}
                </button>
            </header>

            <ErrorNote error={error} onRetry={load} />

            {/* ---- Profile ---- */}
            <HudPanel
                title="Risk profile"
                subtitle={profile?.summary}
                glow={!profile?.is_default}
                right={
                    profile?.is_default ? (
                        <span className="badge-alert">default</span>
                    ) : (
                        <span className="badge-primary">{profile?.label}</span>
                    )
                }
            >
                {profile?.is_default ? (
                    <Empty
                        icon={ShieldCheck}
                        title="No assessment taken"
                        hint={profile.note}
                        action={
                            <button onClick={() => setQuizOpen(true)} className="btn-primary mt-2">
                                Start — 10 questions
                            </button>
                        }
                    />
                ) : (
                    <div className="grid gap-4 md:grid-cols-4">
                        <Stat label="Profile" value={profile.label} mono={false} tone="primary" />
                        <div>
                            <div className="hud-label">Capacity vs tolerance</div>
                            <div className="mt-2 space-y-2">
                                <div className="flex items-center gap-2">
                                    <span className="w-16 font-mono text-[10px] text-dark-500">CAP</span>
                                    <Meter value={profile.capacity_score} tone="primary" />
                                    <span className="w-8 font-mono text-[10px] text-dark-400">
                                        {Math.round(profile.capacity_score)}
                                    </span>
                                </div>
                                <div className="flex items-center gap-2">
                                    <span className="w-16 font-mono text-[10px] text-dark-500">TOL</span>
                                    <Meter value={profile.tolerance_score} tone="alert" />
                                    <span className="w-8 font-mono text-[10px] text-dark-400">
                                        {Math.round(profile.tolerance_score)}
                                    </span>
                                </div>
                            </div>
                        </div>
                        <Stat label="Horizon" value={`${profile.horizon_years} yrs`} />
                        <Stat label="Drawdown tolerance" value={profile.max_drawdown_tolerance} tone="alert" />
                    </div>
                )}
            </HudPanel>

            {/* ---- Gaps ---- */}
            <HudPanel
                title="What you're missing"
                subtitle="Asset classes with no exposure, ranked by the size of the hole"
            >
                {gaps?.gaps?.length > 0 ? (
                    <div className="space-y-3">
                        {gaps.gaps.map((gap) => (
                            <div
                                key={gap.asset_class}
                                className="rounded border border-dark-700/60 p-3 transition-colors hover:border-primary-400/30"
                            >
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div className="flex items-center gap-2">
                                        <span className="font-mono text-sm uppercase text-primary-300">
                                            {gap.asset_class}
                                        </span>
                                        {gap.never_held && <span className="badge-alert">never held</span>}
                                    </div>
                                    <span className="font-mono text-xs tabular-nums text-dark-400">
                                        {gap.current_weight}% → {gap.target_weight}%
                                        <span className="ml-2 text-alert-400">
                                            +{gap.shortfall_percent}pt
                                        </span>
                                    </span>
                                </div>
                                <p className="mt-1.5 text-xs leading-relaxed text-dark-400">{gap.why}</p>
                                <div className="mt-2 flex flex-wrap gap-1.5">
                                    {gap.candidates.map((c) => (
                                        <button
                                            key={c.symbol}
                                            onClick={() => checkSuitability(c.symbol)}
                                            className="badge-primary transition-all hover:border-primary-400/70"
                                            title={c.name}
                                        >
                                            {c.symbol}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <Empty
                        icon={Check}
                        title="No meaningful gaps"
                        hint="Your allocation is close to target across every asset class."
                    />
                )}
            </HudPanel>

            {/* ---- Suitability result ---- */}
            {(checking || checkResult) && (
                <HudPanel title="Suitability check" glow>
                    {checking ? (
                        <Loading rows={2} label={`Assessing ${checking}`} />
                    ) : checkResult.error ? (
                        <ErrorNote error={checkResult.error} />
                    ) : (
                        <div className="space-y-4">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <div className="flex items-center gap-3">
                                    <span className="font-mono text-xl text-dark-50">{checkResult.symbol}</span>
                                    <span className={VERDICT_STYLE[checkResult.verdict]}>
                                        {checkResult.verdict.replace(/_/g, ' ')}
                                    </span>
                                </div>
                                <Stat label="Score" value={checkResult.score} tone="primary" />
                            </div>

                            <p className="text-sm text-dark-200">{checkResult.summary}</p>

                            <div className="space-y-2">
                                {Object.entries(checkResult.factors).map(([name, factor]) => (
                                    <div key={name} className="flex items-center gap-3">
                                        <span className="w-40 shrink-0 font-mono text-[10px] uppercase tracking-wider text-dark-500">
                                            {name.replace(/_/g, ' ')}
                                        </span>
                                        <div className="w-24 shrink-0">
                                            <Meter
                                                value={factor.score}
                                                tone={
                                                    factor.score >= 70
                                                        ? 'success'
                                                        : factor.score >= 40
                                                          ? 'alert'
                                                          : 'danger'
                                                }
                                            />
                                        </div>
                                        <span className="w-8 shrink-0 font-mono text-[10px] tabular-nums text-dark-400">
                                            {Math.round(factor.score)}
                                        </span>
                                        <span className="flex-1 text-[11px] leading-snug text-dark-400">
                                            {factor.reason}
                                        </span>
                                    </div>
                                ))}
                            </div>

                            <p className="border-t border-dark-800 pt-2 text-[10px] uppercase tracking-widest text-dark-600">
                                {checkResult.disclaimer}
                            </p>
                        </div>
                    )}
                </HudPanel>
            )}

            {/* ---- Recommendations ---- */}
            <HudPanel
                title="Ranked for you"
                subtitle="By suitability and the gap each one fills — not by popularity"
                padded={false}
            >
                <div className="divide-y divide-dark-800/60">
                    {recommended.map((r) => (
                        <button
                            key={r.symbol}
                            onClick={() => checkSuitability(r.symbol)}
                            className="flex w-full items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-primary-500/5"
                        >
                            <div className="w-28 shrink-0">
                                <div className="font-mono text-sm text-dark-100">{r.symbol}</div>
                                <div className="font-mono text-[10px] uppercase text-dark-600">
                                    {r.asset_class}
                                </div>
                            </div>
                            <div className="min-w-0 flex-1">
                                <div className="truncate text-xs text-dark-300">{r.name}</div>
                                <div className="truncate text-[11px] text-dark-500">{r.reason}</div>
                            </div>
                            <div className="w-20 shrink-0">
                                <Meter value={r.suitability_score} tone="primary" />
                                <div className="mt-1 text-right font-mono text-[10px] tabular-nums text-dark-500">
                                    {r.suitability_score}
                                </div>
                            </div>
                            <ArrowRight className="h-3.5 w-3.5 shrink-0 text-dark-600" />
                        </button>
                    ))}
                </div>
            </HudPanel>

            {quizOpen && (
                <RiskQuiz
                    onClose={() => setQuizOpen(false)}
                    onComplete={() => {
                        setQuizOpen(false)
                        load()
                    }}
                />
            )}
        </div>
    )
}

/** The 10-question assessment, one question at a time. */
function RiskQuiz({ onClose, onComplete }) {
    const [questions, setQuestions] = useState([])
    const [answers, setAnswers] = useState({})
    const [index, setIndex] = useState(0)
    const [result, setResult] = useState(null)
    const [submitting, setSubmitting] = useState(false)

    useEffect(() => {
        discoverApi.profileQuestions().then(({ data }) => setQuestions(data.questions))
    }, [])

    const question = questions[index]
    const done = questions.length > 0 && Object.keys(answers).length === questions.length

    const choose = (optionIndex) => {
        setAnswers((prev) => ({ ...prev, [question.id]: optionIndex }))
        if (index < questions.length - 1) setTimeout(() => setIndex(index + 1), 180)
    }

    const submit = async () => {
        setSubmitting(true)
        try {
            const { data } = await discoverApi.assessProfile(answers)
            setResult(data)
        } finally {
            setSubmitting(false)
        }
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-dark-950/85 p-4 backdrop-blur-sm">
            <div className="hud-panel-glow hud-corners max-h-[88vh] w-full max-w-2xl overflow-y-auto">
                <header className="flex items-center justify-between border-b border-primary-400/10 px-5 py-3.5">
                    <h2 className="hud-title">
                        {result ? 'Your profile' : `Risk assessment — ${index + 1}/${questions.length || 10}`}
                    </h2>
                    <button onClick={onClose} className="text-dark-500 hover:text-dark-200">
                        <X className="h-4 w-4" />
                    </button>
                </header>

                {result ? (
                    <div className="space-y-5 p-5">
                        <div className="text-center">
                            <div className="font-mono text-3xl text-primary-300 text-glow">{result.label}</div>
                            <p className="mx-auto mt-2 max-w-md text-sm text-dark-400">{result.summary}</p>
                        </div>

                        <div className="grid grid-cols-3 gap-4">
                            <Stat label="Capacity" value={result.capacity_score} tone="primary" />
                            <Stat label="Tolerance" value={result.tolerance_score} tone="alert" />
                            <Stat
                                label="Bound by"
                                value={result.binding_constraint}
                                mono={false}
                                tone="muted"
                            />
                        </div>

                        {result.mismatch && (
                            <div className="rounded border border-alert-400/30 bg-alert-500/8 p-3">
                                <p className="text-xs leading-relaxed text-alert-200">{result.mismatch}</p>
                            </div>
                        )}

                        <div>
                            <div className="hud-label mb-2">Target allocation</div>
                            <div className="space-y-1.5">
                                {Object.entries(result.target_allocation)
                                    .filter(([, v]) => v > 0)
                                    .sort((a, b) => b[1] - a[1])
                                    .map(([cls, weight]) => (
                                        <div key={cls} className="flex items-center gap-3">
                                            <span className="w-16 font-mono text-[11px] uppercase text-dark-300">
                                                {cls}
                                            </span>
                                            <Meter value={weight} max={70} tone="primary" />
                                            <span className="w-10 text-right font-mono text-[11px] text-dark-400">
                                                {weight}%
                                            </span>
                                        </div>
                                    ))}
                            </div>
                        </div>

                        <p className="text-[10px] leading-relaxed text-dark-600">{result.disclaimer}</p>

                        <button onClick={onComplete} className="btn-primary w-full">
                            Apply to my portfolio
                        </button>
                    </div>
                ) : question ? (
                    <div className="p-5">
                        <div className="mb-4 h-0.5 w-full overflow-hidden rounded bg-dark-800">
                            <div
                                className="h-full bg-primary-400 transition-all duration-500"
                                style={{ width: `${((index + 1) / questions.length) * 100}%` }}
                            />
                        </div>

                        <span className="badge-muted mb-3">{question.dimension}</span>
                        <h3 className="text-base font-medium text-dark-50">{question.question}</h3>
                        {question.help && (
                            <p className="mt-1.5 text-xs leading-relaxed text-dark-500">{question.help}</p>
                        )}

                        <div className="mt-4 space-y-2">
                            {question.options.map((option) => {
                                const selected = answers[question.id] === option.index
                                return (
                                    <button
                                        key={option.index}
                                        onClick={() => choose(option.index)}
                                        className={`w-full rounded border px-4 py-3 text-left text-sm transition-all ${
                                            selected
                                                ? 'border-primary-400/60 bg-primary-500/12 text-primary-100 shadow-glow'
                                                : 'border-dark-700/70 text-dark-300 hover:border-primary-400/35 hover:text-dark-100'
                                        }`}
                                    >
                                        {option.label}
                                    </button>
                                )
                            })}
                        </div>

                        <div className="mt-5 flex items-center justify-between">
                            <button
                                onClick={() => setIndex((i) => Math.max(0, i - 1))}
                                disabled={index === 0}
                                className="btn-ghost"
                            >
                                Back
                            </button>
                            {done ? (
                                <button onClick={submit} disabled={submitting} className="btn-primary">
                                    <Sparkles className="h-3.5 w-3.5" />
                                    {submitting ? 'Scoring…' : 'See my profile'}
                                </button>
                            ) : (
                                <button
                                    onClick={() => setIndex((i) => Math.min(questions.length - 1, i + 1))}
                                    disabled={index >= questions.length - 1}
                                    className="btn-ghost"
                                >
                                    Skip
                                </button>
                            )}
                        </div>
                    </div>
                ) : (
                    <div className="p-5">
                        <Loading rows={3} label="Loading assessment" />
                    </div>
                )}
            </div>
        </div>
    )
}

import { useEffect, useState } from 'react'
import { AlertTriangle, BookOpen, Check, GraduationCap, Sparkles, X } from 'lucide-react'
import { HudPanel, Empty, Loading, ErrorNote } from '../components/hud/HudPanel'
import { discoverApi } from '../services/api'

/**
 * Learn — product education built around failure modes.
 *
 * The lesson content is static and reviewable. Only the "for my portfolio"
 * explanation is model-generated, and it's given the verified facts as context
 * so it can't invent a tax rule.
 */

export default function Learn() {
    const [index, setIndex] = useState(null)
    const [topic, setTopic] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    useEffect(() => {
        discoverApi
            .learnIndex()
            .then(({ data }) => setIndex(data))
            .catch((err) => setError(err?.response?.data?.detail || err.message))
            .finally(() => setLoading(false))
    }, [])

    const open = async (topicId) => {
        setTopic({ loading: true })
        try {
            const { data } = await discoverApi.learnTopic(topicId)
            setTopic(data)
        } catch (err) {
            setTopic({ error: err?.response?.data?.detail || err.message })
        }
    }

    if (loading) return <Loading rows={5} label="Loading curriculum" />

    return (
        <div className="space-y-5">
            <header>
                <h1 className="font-mono text-lg uppercase tracking-[0.3em] text-primary-300 text-glow">
                    Learn
                </h1>
                <p className="mt-1 text-xs text-dark-400">
                    What each instrument is, and — more usefully — how it goes wrong.
                </p>
            </header>

            <ErrorNote error={error} />

            {index?.recommended_path?.path?.length > 0 && (
                <HudPanel
                    title="Your path"
                    subtitle="Ordered by what's missing from your portfolio"
                    glow
                >
                    <div className="space-y-2">
                        {index.recommended_path.path.map((step, i) => (
                            <button
                                key={step.id}
                                onClick={() => open(step.id)}
                                className="flex w-full items-center gap-3 rounded border border-dark-700/60 px-3 py-2.5 text-left transition-all hover:border-primary-400/40"
                            >
                                <span
                                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-mono text-[10px] ${
                                        step.already_held
                                            ? 'bg-dark-800 text-dark-500'
                                            : 'bg-primary-500/20 text-primary-300'
                                    }`}
                                >
                                    {i + 1}
                                </span>
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-sm text-dark-100">{step.name}</div>
                                    <div className="truncate text-[11px] text-dark-500">{step.reason}</div>
                                </div>
                                <span className={step.already_held ? 'badge-muted' : 'badge-alert'}>
                                    {step.already_held ? 'held' : 'gap'}
                                </span>
                                <span className="shrink-0 font-mono text-[10px] text-dark-600">
                                    {step.read_minutes}m
                                </span>
                            </button>
                        ))}
                    </div>
                </HudPanel>
            )}

            <HudPanel title="All lessons" padded={false}>
                <div className="grid gap-px bg-dark-800/40 sm:grid-cols-2 lg:grid-cols-3">
                    {(index?.topics || []).map((t) => (
                        <button
                            key={t.id}
                            onClick={() => open(t.id)}
                            className="bg-dark-900/60 p-4 text-left transition-colors hover:bg-primary-500/5"
                        >
                            <div className="flex items-start justify-between gap-2">
                                <BookOpen className="h-4 w-4 shrink-0 text-primary-400/70" />
                                <span className="badge-muted">{t.level}</span>
                            </div>
                            <h3 className="mt-2.5 text-sm font-medium text-dark-100">{t.name}</h3>
                            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-dark-500">
                                {t.one_liner}
                            </p>
                            <div className="mt-2.5 flex items-center gap-3 font-mono text-[10px] text-dark-600">
                                <span>{t.read_minutes} min</span>
                                {t.quiz_questions > 0 && <span>{t.quiz_questions} questions</span>}
                            </div>
                        </button>
                    ))}
                </div>
            </HudPanel>

            {topic && <TopicModal topic={topic} onClose={() => setTopic(null)} />}
        </div>
    )
}

function TopicModal({ topic, onClose }) {
    const [tab, setTab] = useState('lesson')
    const [personal, setPersonal] = useState(null)
    const [quiz, setQuiz] = useState(null)
    const [quizAnswers, setQuizAnswers] = useState({})
    const [quizResult, setQuizResult] = useState(null)
    const [busy, setBusy] = useState(false)

    if (topic.loading) {
        return (
            <Shell onClose={onClose} title="Loading">
                <Loading rows={3} />
            </Shell>
        )
    }
    if (topic.error) {
        return (
            <Shell onClose={onClose} title="Error">
                <ErrorNote error={topic.error} />
            </Shell>
        )
    }

    const loadPersonal = async () => {
        setTab('for-me')
        if (personal) return
        setBusy(true)
        try {
            const { data } = await discoverApi.learnForMe(topic.id)
            setPersonal(data)
        } catch (err) {
            setPersonal({ error: err?.response?.data?.detail || err.message })
        } finally {
            setBusy(false)
        }
    }

    const loadQuiz = async () => {
        setTab('quiz')
        if (quiz) return
        const { data } = await discoverApi.quiz(topic.id)
        setQuiz(data.questions)
    }

    const submitQuiz = async () => {
        setBusy(true)
        try {
            const { data } = await discoverApi.submitQuiz(topic.id, quizAnswers)
            setQuizResult(data)
        } finally {
            setBusy(false)
        }
    }

    return (
        <Shell onClose={onClose} title={topic.name}>
            <div className="flex gap-1 border-b border-primary-400/10 px-5">
                {[
                    ['lesson', 'Lesson'],
                    ['for-me', 'For my portfolio'],
                    ['quiz', 'Check yourself'],
                ].map(([id, label]) => (
                    <button
                        key={id}
                        onClick={() => (id === 'for-me' ? loadPersonal() : id === 'quiz' ? loadQuiz() : setTab('lesson'))}
                        className={`border-b-2 px-3 py-2.5 font-mono text-[10px] uppercase tracking-[0.15em] transition-colors ${
                            tab === id
                                ? 'border-primary-400 text-primary-300'
                                : 'border-transparent text-dark-500 hover:text-dark-300'
                        }`}
                    >
                        {label}
                    </button>
                ))}
            </div>

            <div className="space-y-5 p-5">
                {tab === 'lesson' && (
                    <>
                        <p className="text-sm leading-relaxed text-dark-200">{topic.what_it_is}</p>

                        <Section title="How you earn">
                            <ul className="space-y-1.5">
                                {topic.how_you_earn.map((item, i) => (
                                    <li key={i} className="flex gap-2 text-sm text-dark-300">
                                        <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success-400" />
                                        {item}
                                    </li>
                                ))}
                            </ul>
                        </Section>

                        <Section title="How it goes wrong" tone="danger">
                            <ul className="space-y-2">
                                {topic.how_it_goes_wrong.map((item, i) => (
                                    <li key={i} className="flex gap-2 text-sm leading-relaxed text-dark-300">
                                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger-400" />
                                        {item}
                                    </li>
                                ))}
                            </ul>
                        </Section>

                        <Section title="What to watch">
                            <div className="space-y-2">
                                {topic.key_metrics.map((m) => (
                                    <div key={m.name} className="rounded border border-dark-700/60 px-3 py-2">
                                        <div className="font-mono text-xs text-primary-300">{m.name}</div>
                                        <div className="mt-0.5 text-xs leading-relaxed text-dark-400">{m.why}</div>
                                    </div>
                                ))}
                            </div>
                        </Section>

                        <div className="grid gap-4 sm:grid-cols-2">
                            <Section title="Suits">
                                <p className="text-sm text-dark-300">{topic.who_its_for}</p>
                            </Section>
                            <Section title="Avoid if">
                                <p className="text-sm text-dark-300">{topic.who_should_avoid}</p>
                            </Section>
                        </div>

                        <Section title="Tax in India">
                            <p className="text-sm leading-relaxed text-dark-300">{topic.taxation_india}</p>
                        </Section>

                        <Section title="Indian context">
                            <p className="text-sm leading-relaxed text-dark-300">{topic.india_context}</p>
                        </Section>

                        {topic.available_instruments?.length > 0 && (
                            <Section title="Listed on NSE">
                                <div className="flex flex-wrap gap-1.5">
                                    {topic.available_instruments.map((i) => (
                                        <span key={i.symbol} className="badge-primary" title={i.name}>
                                            {i.symbol}
                                        </span>
                                    ))}
                                </div>
                            </Section>
                        )}
                    </>
                )}

                {tab === 'for-me' &&
                    (busy ? (
                        <Loading rows={3} label="Reading your portfolio" />
                    ) : personal?.error ? (
                        <ErrorNote error={personal.error} />
                    ) : personal?.explanation ? (
                        <div className="space-y-2 text-sm leading-relaxed text-dark-200">
                            {personal.explanation.split('\n').map((line, i) =>
                                line.trim() ? (
                                    <p key={i}>{line.replace(/\*\*/g, '').replace(/^[-*]\s*/, '• ')}</p>
                                ) : null
                            )}
                            <p className="pt-2 font-mono text-[10px] uppercase tracking-widest text-dark-600">
                                {personal.meta?.provider} · grounded in the lesson above
                            </p>
                        </div>
                    ) : (
                        <Empty icon={Sparkles} title="Nothing yet" hint={personal?.fallback} />
                    ))}

                {tab === 'quiz' &&
                    (quizResult ? (
                        <div className="space-y-4">
                            <div className="text-center">
                                <div className="font-mono text-3xl text-primary-300">
                                    {quizResult.score}/{quizResult.total}
                                </div>
                                <p className="mt-1 text-xs text-dark-400">{quizResult.percent}% correct</p>
                            </div>
                            {quizResult.results.map((r) => (
                                <div
                                    key={r.index}
                                    className={`rounded border p-3 ${
                                        r.correct
                                            ? 'border-success-400/30 bg-success-500/8'
                                            : 'border-danger-400/30 bg-danger-500/8'
                                    }`}
                                >
                                    <p className="text-sm text-dark-100">{r.question}</p>
                                    {!r.correct && (
                                        <p className="mt-1.5 text-xs text-dark-400">
                                            Correct: <span className="text-success-400">{r.correct_answer}</span>
                                        </p>
                                    )}
                                    <p className="mt-1.5 text-xs leading-relaxed text-dark-400">
                                        {r.explanation}
                                    </p>
                                </div>
                            ))}
                            <button
                                onClick={() => {
                                    setQuizResult(null)
                                    setQuizAnswers({})
                                }}
                                className="btn-ghost w-full"
                            >
                                Try again
                            </button>
                        </div>
                    ) : quiz ? (
                        <div className="space-y-5">
                            {quiz.map((q) => (
                                <div key={q.index}>
                                    <p className="text-sm text-dark-100">{q.question}</p>
                                    <div className="mt-2 space-y-1.5">
                                        {q.options.map((option, oi) => (
                                            <button
                                                key={oi}
                                                onClick={() =>
                                                    setQuizAnswers((prev) => ({ ...prev, [q.index]: oi }))
                                                }
                                                className={`w-full rounded border px-3 py-2 text-left text-xs transition-all ${
                                                    quizAnswers[q.index] === oi
                                                        ? 'border-primary-400/60 bg-primary-500/12 text-primary-100'
                                                        : 'border-dark-700/70 text-dark-300 hover:border-primary-400/35'
                                                }`}
                                            >
                                                {option}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            ))}
                            <button
                                onClick={submitQuiz}
                                disabled={busy || Object.keys(quizAnswers).length < quiz.length}
                                className="btn-primary w-full"
                            >
                                <GraduationCap className="h-3.5 w-3.5" /> Submit
                            </button>
                        </div>
                    ) : (
                        <Loading rows={2} label="Loading questions" />
                    ))}
            </div>
        </Shell>
    )
}

function Shell({ title, onClose, children }) {
    return (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-dark-950/85 p-4 backdrop-blur-sm">
            <div className="hud-panel-glow hud-corners my-6 w-full max-w-3xl">
                <header className="sticky top-0 z-10 flex items-center justify-between border-b border-primary-400/10 bg-dark-900/95 px-5 py-3.5 backdrop-blur">
                    <h2 className="hud-title truncate">{title}</h2>
                    <button onClick={onClose} className="shrink-0 text-dark-500 hover:text-dark-200">
                        <X className="h-4 w-4" />
                    </button>
                </header>
                {children}
            </div>
        </div>
    )
}

function Section({ title, children, tone }) {
    return (
        <div>
            <h4
                className={`hud-label mb-2 ${tone === 'danger' ? '!text-danger-400/80' : ''}`}
            >
                {title}
            </h4>
            {children}
        </div>
    )
}

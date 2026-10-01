import { useState, useEffect } from 'react'
import axios from 'axios'
import { Dna, Brain, Sparkles, CheckCircle } from 'lucide-react'

const API = 'http://localhost:8000/api/novel/dna'

export default function DNAMatching() {
    const [quiz, setQuiz] = useState([])
    const [answers, setAnswers] = useState({})
    const [profile, setProfile] = useState(null)
    const [matches, setMatches] = useState([])
    const [tab, setTab] = useState('quiz')
    const [loading, setLoading] = useState(false)
    const [submitted, setSubmitted] = useState(false)

    useEffect(() => {
        fetchQuiz()
        fetchProfile()
    }, [])

    const fetchQuiz = async () => {
        try {
            const res = await axios.get(`${API}/quiz`)
            setQuiz(res.data)
        } catch (err) { console.error(err) }
    }

    const fetchProfile = async () => {
        try {
            const res = await axios.get(`${API}/profile`)
            if (!res.data.error) {
                setProfile(res.data)
                setSubmitted(true)
            }
        } catch (err) { /** No profile yet */ }
    }

    const fetchMatches = async () => {
        try {
            setLoading(true)
            const res = await axios.get(`${API}/matches`)
            setMatches(res.data)
        } catch (err) { console.error(err) }
        finally { setLoading(false) }
    }

    const submitQuiz = async () => {
        try {
            setLoading(true)
            const res = await axios.post(`${API}/quiz`, { answers })
            setProfile(res.data)
            setSubmitted(true)
            setTab('profile')
        } catch (err) { console.error(err) }
        finally { setLoading(false) }
    }

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                    <Dna className="w-7 h-7 text-violet-400" />
                    Stock DNA Matching
                </h1>
                <p className="text-dark-400 mt-1">Match stocks to your investor personality</p>
            </div>

            <div className="flex gap-2">
                {['quiz', 'profile', 'matches'].map(t => (
                    <button key={t} onClick={() => { setTab(t); if (t === 'matches') fetchMatches() }}
                        className={`px-4 py-2 rounded-lg font-medium transition-all ${tab === t ? 'bg-violet-600 text-white' : 'bg-dark-700 text-dark-400 hover:text-white'
                            }`}>
                        {t === 'quiz' ? '📋 Quiz' : t === 'profile' ? '🧬 Your DNA' : '💕 Matches'}
                    </button>
                ))}
            </div>

            {tab === 'quiz' && (
                <div className="space-y-4">
                    {quiz.map((q, qi) => (
                        <div key={q.id} className="glass-card p-5">
                            <h3 className="text-white font-semibold mb-3">
                                {qi + 1}. {q.question}
                            </h3>
                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                                {q.options.map((opt, oi) => (
                                    <button key={oi}
                                        onClick={() => setAnswers(prev => ({ ...prev, [q.id]: oi }))}
                                        className={`px-4 py-3 rounded-lg text-sm text-left transition-all flex items-center gap-2 ${answers[q.id] === oi
                                                ? 'bg-violet-600/30 border border-violet-500 text-violet-300'
                                                : 'bg-dark-700 border border-dark-600 text-dark-300 hover:border-dark-500'
                                            }`}>
                                        {answers[q.id] === oi && <CheckCircle className="w-4 h-4 text-violet-400 shrink-0" />}
                                        {opt}
                                    </button>
                                ))}
                            </div>
                        </div>
                    ))}
                    {quiz.length > 0 && (
                        <button onClick={submitQuiz} disabled={Object.keys(answers).length < quiz.length}
                            className="w-full py-3 bg-violet-600 text-white rounded-lg font-semibold hover:bg-violet-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all">
                            {loading ? 'Analyzing...' : 'Discover My Investor DNA'}
                        </button>
                    )}
                </div>
            )}

            {tab === 'profile' && profile && (
                <div className="glass-card p-8 text-center">
                    <Brain className="w-16 h-16 text-violet-400 mx-auto mb-4" />
                    <h2 className="text-3xl font-bold text-white mb-2 capitalize">{profile.personality_type?.replace('_', ' ')}</h2>
                    <p className="text-dark-300 text-lg mb-6">{profile.description}</p>
                    <div className="inline-flex items-center gap-2 bg-violet-500/20 px-6 py-3 rounded-full">
                        <span className="text-violet-400 font-semibold">Risk Score:</span>
                        <span className="text-white text-2xl font-bold">{profile.risk_score?.toFixed(0)}/100</span>
                    </div>
                    {profile.breakdown && (
                        <div className="mt-6 grid grid-cols-2 md:grid-cols-3 gap-3">
                            {Object.entries(profile.breakdown).map(([key, val]) => (
                                <div key={key} className="bg-dark-700/50 rounded-lg p-3">
                                    <div className="text-xs text-dark-400 capitalize">{key.replace('_', ' ')}</div>
                                    <div className="text-white text-sm">{val.selected}</div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {tab === 'matches' && (
                <div className="space-y-3">
                    {loading ? (
                        <div className="text-center text-dark-400 py-12">🔍 Finding your perfect stock matches...</div>
                    ) : !submitted ? (
                        <div className="glass-card p-8 text-center text-dark-400">
                            <Sparkles className="w-12 h-12 text-amber-400 mx-auto mb-4" />
                            Take the quiz first to get personalized matches!
                        </div>
                    ) : matches.map((m, i) => (
                        <div key={i} className="glass-card p-5 flex items-center justify-between">
                            <div>
                                <span className="text-white font-semibold text-lg">{m.symbol}</span>
                                <span className="text-dark-400 text-sm ml-3">{m.stock_personality}</span>
                                <p className="text-dark-500 text-sm mt-1">{m.why_match}</p>
                            </div>
                            <div className="text-right">
                                <div className="text-2xl font-bold text-violet-400">{m.match_score}%</div>
                                <div className="text-xs text-dark-500">Match</div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

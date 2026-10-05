import axios from 'axios'

/**
 * Where the backend lives.
 *
 *   VITE_API_URL   explicit override, for any custom deployment
 *   file://        Electron — always talk to the local backend directly
 *   port 5173/5174 Vite dev server — backend is a separate process on :8000
 *   anything else  served by nginx, which proxies /api on the same origin
 */
function resolveApiBase() {
    if (import.meta.env?.VITE_API_URL) return import.meta.env.VITE_API_URL

    if (typeof window === 'undefined') return 'http://localhost:8000/api'

    const { protocol, hostname, port } = window.location
    if (protocol === 'file:') return 'http://localhost:8000/api'
    if (port === '5173' || port === '5174') return `http://${hostname}:8000/api`

    return '/api'
}

const API_BASE_URL = resolveApiBase()
export const API_ROOT = API_BASE_URL.replace(/\/api$/, '')

const api = axios.create({
    baseURL: API_BASE_URL,
    timeout: 30000,
    headers: {
        'Content-Type': 'application/json'
    }
})

// Stock APIs
export const stockApi = {
    search: (query) => api.get(`/stocks/search?q=${query}`),
    getDetails: (symbol, exchange = 'NSE') => api.get(`/stocks/${symbol}?exchange=${exchange}`),
    getPrice: (symbol, exchange = 'NSE') => api.get(`/stocks/${symbol}/price?exchange=${exchange}`),
    getHistorical: (symbol, period = '1y', exchange = 'NSE') =>
        api.get(`/stocks/${symbol}/historical?period=${period}&exchange=${exchange}`),
    getFundamentals: (symbol, exchange = 'NSE') => api.get(`/stocks/${symbol}/fundamentals?exchange=${exchange}`),
    getTechnicals: (symbol, exchange = 'NSE') => api.get(`/stocks/${symbol}/technicals?exchange=${exchange}`),
    getReversal: (symbol, exchange = 'NSE') => api.get(`/stocks/${symbol}/reversal?exchange=${exchange}`),
    getChartData: (symbol, period = '6mo', exchange = 'NSE') =>
        api.get(`/stocks/${symbol}/chart-data?period=${period}&exchange=${exchange}&include_indicators=true`),
    // New AI-powered endpoints
    getAIRecommendation: (symbol, strategy = 'balanced', exchange = 'NSE') =>
        api.get(`/stocks/${symbol}/ai-recommendation?strategy=${strategy}&exchange=${exchange}`),
    getQualityScore: (symbol, exchange = 'NSE') => api.get(`/stocks/${symbol}/quality-score?exchange=${exchange}`),
    getSentiment: (symbol, exchange = 'NSE') => api.get(`/stocks/${symbol}/sentiment?exchange=${exchange}`),
    getSuitability: (symbol, exchange = 'NSE') => api.get(`/stocks/${symbol}/suitability?exchange=${exchange}`),
}

// Fly brain: takes watchtower's openings and paper trades them, learning from each close
export const flyBrainApi = {
    dashboard: () => api.get('/flybrain/dashboard', { timeout: 60000 }),
    setEnabled: (enabled) => api.post('/flybrain/enabled', { enabled }),
    scan: () => api.post('/flybrain/scan'),
    history: () => api.get('/flybrain/history'),
    runHistory: () => api.post('/flybrain/history/run'),
    today: () => api.get('/flybrain/suggestions', { timeout: 120000 }),
    monteCarlo: (source, fractionPct) =>
        api.get('/flybrain/lab/montecarlo', { params: { source, fraction_pct: fractionPct }, timeout: 120000 }),
    csvSampleUrl: () => `${API_BASE_URL}/flybrain/lab/csv/sample`,
    runCsv: (file, startFrom, trainLive) => {
        const form = new FormData()
        form.append('file', file)
        form.append('start_from', startFrom)
        form.append('train_live', trainLive ? 'true' : 'false')
        return api.post('/flybrain/lab/csv', form, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 })
    },
    csvJob: (id) => api.get(`/flybrain/lab/csv/job/${id}`),
    csvRuns: () => api.get('/flybrain/lab/csv/runs'),
    csvRun: (id) => api.get(`/flybrain/lab/csv/runs/${id}`),
}

// Portfolio APIs
export const portfolioApi = {
    getAll: () => api.get('/portfolio'),
    getShortTerm: () => api.get('/portfolio/short-term'),
    getLongTerm: () => api.get('/portfolio/long-term'),
    add: (data) => api.post('/portfolio/add', data),
    update: (id, data) => api.put(`/portfolio/${id}`, data),
    changeStrategy: (id, strategy) => api.put(`/portfolio/${id}/change-strategy?new_strategy=${strategy}`),
    remove: (id, sellPrice = null) => api.delete(`/portfolio/${id}${sellPrice ? `?sell_price=${sellPrice}` : ''}`),
    getPerformance: () => api.get('/portfolio/performance'),
}

// Paper Trading APIs
export const paperTradingApi = {
    getAccount: () => api.get('/paper-trading/account'),
    getPortfolio: () => api.get('/paper-trading/portfolio'),
    buy: (data) => api.post('/paper-trading/buy', data),
    sell: (data) => api.post('/paper-trading/sell', data),
    getHistory: (strategy = null, limit = 50) =>
        api.get(`/paper-trading/history?limit=${limit}${strategy ? `&strategy=${strategy}` : ''}`),
    getPerformance: () => api.get('/paper-trading/performance'),
    reset: (capital = 1000000) => api.post(`/paper-trading/reset?starting_capital=${capital}`),
}

// Technical Analysis APIs
export const technicalsApi = {
    getIndicators: (symbol, exchange = 'NSE', period = '1y') =>
        api.get(`/technicals/${symbol}?exchange=${exchange}&period=${period}`),
    getReversal: (symbol, exchange = 'NSE') => api.get(`/technicals/${symbol}/reversal?exchange=${exchange}`),
    getSummary: (symbol, exchange = 'NSE') => api.get(`/technicals/${symbol}/summary?exchange=${exchange}`),
}

// Backtest APIs
export const backtestApi = {
    run: (data) => api.post('/backtest/run', data),
    getHistory: (limit = 20) => api.get(`/backtest/history?limit=${limit}`),
    getResult: (id) => api.get(`/backtest/results/${id}`),
}

// Chat APIs
export const chatApi = {
    sendMessage: (message, sessionId = 'default') =>
        api.post('/chat/message', { message, session_id: sessionId }),
    getHistory: (sessionId = 'default', limit = 50) =>
        api.get(`/chat/history?session_id=${sessionId}&limit=${limit}`),
}

// Alerts APIs
export const alertsApi = {
    getAll: () => api.get('/alerts'),
    create: (data) => api.post('/alerts', data),
    delete: (id) => api.delete(`/alerts/${id}`),
    check: () => api.get('/alerts/check'),
    getTriggered: (limit = 20) => api.get(`/alerts/triggered?limit=${limit}`),
}

// Fundamental Analysis APIs
export const fundamentalApi = {
    getDetailed: (symbol) => api.get(`/fundamentals/${symbol}`),
    getPeers: (symbol) => api.get(`/fundamentals/${symbol}/peers`),
    getQuality: (symbol) => api.get(`/fundamentals/${symbol}/quality`),
    getEconomic: () => api.get('/fundamentals/economic-indicators'),
}

// Novel Features APIs
export const novelApi = {
    // Trade Clone
    tradeClone: {
        getPortfolios: () => api.get('/novel/trade-clone/portfolios'),
        getPortfolio: (id) => api.get(`/novel/trade-clone/portfolios/${id}`),
        getRecommendations: () => api.get('/novel/trade-clone/recommendations'),
    },
    // Regret Analyzer
    regret: {
        analyze: (data) => api.post('/novel/regret/analyze', data),
        getTopRegrets: () => api.get('/novel/regret/top-regrets'),
        getPatterns: () => api.get('/novel/regret/patterns'),
    },
    // Market Mood
    mood: {
        getCurrent: () => api.get('/novel/mood/current'),
        getHistory: () => api.get('/novel/mood/history'),
    },
    // Stock DNA
    dna: {
        getQuiz: () => api.get('/novel/dna/quiz'),
        submitQuiz: (answers) => api.post('/novel/dna/quiz', answers),
        getProfile: () => api.get('/novel/dna/profile'),
        getMatches: () => api.get('/novel/dna/matches'),
    },
    // Future You
    future: {
        simulate: (params) => api.post('/novel/future/simulate', params),
    },
    // Margin of Safety
    margin: {
        calculate: (symbol) => api.get(`/novel/margin/${symbol}`),
    },
    // Exit Architect
    exit: {
        getStrategies: (symbol, type = 'short-term') =>
            api.get(`/novel/exit/${symbol}/strategies?strategy_type=${type}`),
        getActive: () => api.get('/novel/exit/active'),
    },
}

// ---------------------------------------------------------------------------
// Tradeo v3 — intelligence, realtime, unified wealth, discovery, setup
// ---------------------------------------------------------------------------

// The brain: conversation, voice, sentiment, per-symbol verdicts
export const aiApi = {
    status: () => api.get('/ai/status'),
    models: () => api.get('/ai/models'),
    ask: (message, sessionId = 'default', channel = 'screen') =>
        api.post('/ai/ask', { message, session_id: sessionId, channel }),
    brief: (symbol, horizon = 'swing') => api.get(`/ai/brief/${symbol}?horizon=${horizon}`),
    opportunity: (symbol) => api.get(`/ai/opportunity/${symbol}`),
    sentiment: (symbol, useX = null) =>
        api.get(`/ai/sentiment/${symbol}${useX === null ? '' : `?use_x=${useX}`}`),
    xSentiment: (symbol, days = 7) => api.get(`/ai/x-sentiment/${symbol}?days=${days}`),
    marketPulse: (days = 1) => api.get(`/ai/market-pulse?days=${days}`),
    resolve: (q) => api.get(`/ai/resolve?q=${encodeURIComponent(q)}`),
    history: (sessionId = 'default') => api.get(`/ai/history?session_id=${sessionId}`),
    clearHistory: (sessionId = 'default') => api.delete(`/ai/history?session_id=${sessionId}`),

    // Voice: one round trip — clean the transcript, answer, shape for speech
    voiceAsk: (message, sessionId = 'voice') =>
        api.post('/ai/voice/ask', { message, session_id: sessionId, channel: 'voice' }),
    // Command routing: rules first (milliseconds, offline), falling through to
    // the conversational brain only when nothing matches.
    voiceCommand: (text, fallbackToChat = true) =>
        api.post('/ai/voice/command', { text, fallback_to_chat: fallbackToChat }, { timeout: 240000 }),
    voiceCommands: () => api.get('/ai/voice/commands'),
    prepareSpeech: (text) => api.post('/ai/voice/prepare', { text }),
    voiceStatus: () => api.get('/ai/voice/status'),
    // Local Piper voice: returns WAV audio.
    speak: (text) => api.post('/ai/voice/speak', { text }, { responseType: 'blob', timeout: 60000 }),
    transcribe: (blob) => {
        const form = new FormData()
        form.append('audio', blob, blob.type === 'audio/wav' ? 'clip.wav' : 'clip.webm')
        return api.post('/ai/voice/transcribe', form, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 120000,
        })
    },

    /**
     * Streaming answers over SSE.
     *
     * Uses fetch rather than EventSource because the endpoint is a POST —
     * EventSource only speaks GET.
     */
    stream: async (message, { sessionId = 'default', channel = 'screen', onEvent, signal } = {}) => {
        const response = await fetch(`${API_BASE_URL}/ai/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, session_id: sessionId, channel }),
            signal,
        })
        if (!response.ok) throw new Error(`Stream failed: ${response.status}`)

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })

            // SSE frames are separated by a blank line; keep any partial tail.
            const frames = buffer.split('\n\n')
            buffer = frames.pop() ?? ''

            for (const frame of frames) {
                const line = frame.split('\n').find((l) => l.startsWith('data:'))
                if (!line) continue
                try {
                    onEvent?.(JSON.parse(line.slice(5).trim()))
                } catch {
                    /* a truncated frame is not worth failing the stream over */
                }
            }
        }
    },
}

// The watchtower: scanner, opportunities, watchlist, Telegram
export const signalsApi = {
    status: () => api.get('/signals/status'),
    market: () => api.get('/signals/market'),
    opportunities: (params = {}) => {
        const q = new URLSearchParams(
            Object.entries(params).filter(([, v]) => v !== null && v !== undefined)
        )
        return api.get(`/signals/opportunities?${q}`)
    },
    scan: (body = { limit: 40, deep: true }) => api.post('/signals/scan', body),
    scanWatchlist: () => api.post('/signals/scan/watchlist'),
    scanHoldings: () => api.post('/signals/scan/holdings'),
    scanHistory: (limit = 10) => api.get(`/signals/scans?limit=${limit}`),
    watchlist: () => api.get('/signals/watchlist'),
    watch: (symbol, note = null) => api.post('/signals/watchlist', { symbol, note }),
    unwatch: (symbol) => api.delete(`/signals/watchlist/${symbol}`),
    notifications: (limit = 50) => api.get(`/signals/notifications?limit=${limit}`),
    telegram: () => api.get('/signals/telegram'),
    telegramTest: (message) => api.post('/signals/telegram/test', { message }),
    engine: (action) => api.post(`/signals/engine/${action}`),
}

// Unified wealth: brokers, depositories, consolidated analytics
export const wealthApi = {
    overview: () => api.get('/wealth/overview', { timeout: 90000 }),
    brokers: () => api.get('/wealth/brokers'),
    brokerProfile: (broker) => api.get(`/wealth/brokers/${broker}/profile`),
    holdings: () => api.get('/wealth/holdings'),
    analytics: () => api.get('/wealth/analytics'),
    review: (channel = 'screen') => api.get(`/wealth/review?channel=${channel}`),
    funds: () => api.get('/wealth/funds'),
    positions: () => api.get('/wealth/positions'),
    depositoryAccounts: () => api.get('/wealth/depository/accounts'),
    depositoryPreview: (file) => {
        const form = new FormData()
        form.append('file', file)
        return api.post('/wealth/depository/preview', form, {
            headers: { 'Content-Type': 'multipart/form-data' },
        })
    },
    depositoryImport: (file, source = 'cdsl', label = 'primary') => {
        const form = new FormData()
        form.append('file', file)
        return api.post(
            `/wealth/depository/import?source=${source}&account_label=${label}`,
            form,
            { headers: { 'Content-Type': 'multipart/form-data' } }
        )
    },
    orderPreview: (order) => api.post('/wealth/orders/preview', order),
}

// Discovery, education, risk profiling, suitability
export const discoverApi = {
    profileQuestions: () => api.get('/discover/profile/questions'),
    assessProfile: (answers, save = true) =>
        api.post('/discover/profile/assess', { answers, save }),
    profile: () => api.get('/discover/profile'),
    profileHistory: () => api.get('/discover/profile/history'),
    universe: (params = {}) => {
        const q = new URLSearchParams(
            Object.entries(params).filter(([, v]) => v !== null && v !== undefined)
        )
        return api.get(`/discover/universe?${q}`)
    },
    gaps: () => api.get('/discover/gaps'),
    recommended: (assetClass = null, limit = 20) =>
        api.get(`/discover/recommended?limit=${limit}${assetClass ? `&asset_class=${assetClass}` : ''}`),
    suitability: (symbol) => api.get(`/discover/suitability/${symbol}`),
    learnIndex: () => api.get('/discover/learn'),
    learnTopic: (topic) => api.get(`/discover/learn/${topic}`),
    quiz: (topic) => api.get(`/discover/learn/${topic}/quiz`),
    submitQuiz: (topic, answers) => api.post(`/discover/learn/${topic}/quiz`, { answers }),
    learnForMe: (topic) => api.get(`/discover/learn/${topic}/for-me`),
}

// Connections and credentials
export const setupApi = {
    config: () => api.get('/setup/config'),
    saveConfig: (values) => api.post('/setup/config', { values }),
    checklist: () => api.get('/setup/checklist'),
    // Live probe of every integration. Runs real network calls in parallel on
    // the server, so give it room.
    diagnostics: () => api.get('/setup/diagnostics', { timeout: 90000 }),
    failures: () => api.get('/setup/failures'),
    voice: () => api.get('/setup/voice'),
    testBroker: (broker) => api.post(`/setup/test/broker/${broker}`),
    testAi: () => api.post('/setup/test/ai'),
    testTelegram: () => api.post('/setup/test/telegram'),
}

export const systemApi = {
    health: () => axios.get(`${API_ROOT}/health`).then((r) => r.data),
}

// Autopilot: proposals, guardrails, paper account
export const autopilotApi = {
    agents: () => api.get('/autopilot/agents', { timeout: 60000 }),
    risk: () => api.get('/autopilot/risk', { timeout: 60000 }),
    updateRisk: (patch) => api.post('/autopilot/risk', patch, { timeout: 60000 }),
    resumeRisk: () => api.post('/autopilot/risk/resume', null, { timeout: 60000 }),
    evaluation: () => api.get('/autopilot/evaluation'),
    runEvaluation: () => api.post('/autopilot/evaluation/run', null, { timeout: 600000 }),
    scheduler: () => api.get('/autopilot/scheduler'),
    updateAgent: (id, patch) => api.post(`/autopilot/agents/${id}`, patch),
    runAgent: (id) => api.post(`/autopilot/agents/${id}/run`, null, { timeout: 180000 }),
    longterm: (refresh = false) => api.get('/autopilot/longterm', { params: { refresh }, timeout: 300000 }),
    // Bracket triggers: paper positions with the exit committed before entry.
    triggers: () => api.get('/autopilot/triggers'),
    triggerHistory: (limit = 20) => api.get(`/autopilot/triggers/history?limit=${limit}`),
    armTrigger: (payload) => api.post('/autopilot/triggers/arm', payload),
    cancelTrigger: (id) => api.post(`/autopilot/triggers/${id}/cancel`),
    sweepTriggers: () => api.post('/autopilot/triggers/sweep'),
    status: () => api.get('/autopilot/status'),
    safety: () => api.get('/autopilot/safety'),
    proposals: (status = null, limit = 30) =>
        api.get(`/autopilot/proposals?limit=${limit}${status ? `&status=${status}` : ''}`),
    proposal: (id) => api.get(`/autopilot/proposals/${id}`),
    run: () => api.post('/autopilot/run'),
    approve: (id, approvedBy = 'operator') =>
        api.post(`/autopilot/proposals/${id}/approve`, { approved_by: approvedBy }),
    reject: (id) => api.post(`/autopilot/proposals/${id}/reject`),
    account: () => api.get('/autopilot/account'),
    trades: (limit = 50) => api.get(`/autopilot/trades?limit=${limit}`),
    performance: () => api.get('/autopilot/performance'),
    reset: (capital = 1000000) => api.post('/autopilot/reset', { capital, confirm: true }),
}

// Strategy Studio: sandboxed custom code, backtests, sweeps, walk-forward.
//
// The run endpoints get their own generous timeouts. A walk-forward is several
// hundred backtests and legitimately takes ten seconds or more; the default 30s
// would abort a sweep that was about to succeed.
export const strategyApi = {
    library: () => api.get('/strategies/library'),
    reference: () => api.get('/strategies/reference'),

    list: () => api.get('/strategies'),
    get: (id) => api.get(`/strategies/${id}`),
    save: (payload) => api.post('/strategies', payload),
    update: (id, payload) => api.patch(`/strategies/${id}`, payload),
    remove: (id) => api.delete(`/strategies/${id}`),
    versions: (id) => api.get(`/strategies/${id}/versions`),

    // Called on a debounce while typing, so it must stay cheap and quiet.
    validate: (source) => api.post('/strategies/validate', { source }),

    backtest: (payload) => api.post('/strategies/run/backtest', payload, { timeout: 90000 }),
    sweep: (payload) => api.post('/strategies/run/sweep', payload, { timeout: 180000 }),
    walkforward: (payload) =>
        api.post('/strategies/run/walkforward', payload, { timeout: 300000 }),
    compare: (payload) => api.post('/strategies/run/compare', payload, { timeout: 180000 }),
    history: (strategyId = null, limit = 40) =>
        api.get(`/strategies/runs/history?limit=${limit}${strategyId ? `&strategy_id=${strategyId}` : ''}`),

    evaluate: (payload) => api.post('/strategies/live/evaluate', payload, { timeout: 90000 }),
    scan: (period = '2y') => api.get(`/strategies/live/scan?period=${period}`, { timeout: 180000 }),
    propose: (payload) => api.post('/strategies/live/propose', payload, { timeout: 90000 }),
}

export default api

// Analysis agents: transparent, step-by-step reasoning with approval gates.
export const agentsApi = {
    list: () => api.get('/agents/'),
    run: (agent, symbol, deep = false) =>
        api.post('/agents/run', { agent, symbol, deep }),
    get: (runId) => api.get(`/agents/run/${runId}`),
    runs: (limit = 20) => api.get(`/agents/runs?limit=${limit}`),
    approve: (runId, approved = true, reason = '') =>
        api.post(`/agents/run/${runId}/approve`, { approved, reason }),
    // Synchronous — safe only in fast mode, which is ~5s. Deep mode streams.
    analyse: (symbol, deep = false) =>
        api.get(`/agents/analyse/${symbol}?deep=${deep}`, { timeout: 200000 }),
    // EventSource needs an absolute URL; it cannot use the axios instance.
    stream: (runId) => new EventSource(`${API_BASE_URL}/agents/stream/${runId}`),
}

// Research engine (agent, swarm teams, factor zoo, backtests, options maths),
// served through Tradeo's backend at /api/research. Every EventSource needs an
// absolute URL, so those bypass the axios instance.
const researchSse = (path) => new EventSource(`${API_BASE_URL}/research${path}`)

export const researchApi = {
    status: () => api.get('/research/status'),
    restart: () => api.post('/research/restart'),
    skills: () => api.get('/research/skills'),
    upload: (file) => {
        const form = new FormData()
        form.append('file', file)
        return api.post('/research/upload', form, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 })
    },

    // Agent sessions
    sessions: (limit = 50) => api.get(`/research/sessions?limit=${limit}`),
    createSession: (title = 'New research') => api.post('/research/sessions', { title }),
    renameSession: (id, title) => api.patch(`/research/sessions/${id}`, { title }),
    deleteSession: (id) => api.delete(`/research/sessions/${id}`),
    messages: (id) => api.get(`/research/sessions/${id}/messages?limit=200`),
    send: (id, content) => api.post(`/research/sessions/${id}/messages`, { content }),
    cancel: (id) => api.post(`/research/sessions/${id}/cancel`),
    events: (id) => researchSse(`/sessions/${id}/events`),

    // Backtest runs
    runs: (limit = 50) => api.get(`/research/runs?limit=${limit}`),
    run: (id) => api.get(`/research/runs/${id}`, { timeout: 60000 }),
    runCode: (id) => api.get(`/research/runs/${id}/code`),
    runPine: (id) => api.get(`/research/runs/${id}/pine`),

    // Swarm: teams of agents run as a DAG
    swarmPresets: () => api.get('/research/swarm/presets'),
    swarmRuns: () => api.get('/research/swarm/runs'),
    swarmRun: (id) => api.get(`/research/swarm/runs/${id}`),
    startSwarm: (preset_name, user_vars) => api.post('/research/swarm/runs', { preset_name, user_vars }),
    cancelSwarm: (id) => api.post(`/research/swarm/runs/${id}/cancel`),
    retrySwarm: (id) => api.post(`/research/swarm/runs/${id}/retry`),
    swarmEvents: (id) => researchSse(`/swarm/runs/${id}/events`),

    // Alpha zoo: 460+ published factors, benchmarked by IC
    alphas: (params = {}) => api.get('/research/alpha/list', { params: { limit: 500, ...params } }),
    alpha: (id) => api.get(`/research/alpha/${encodeURIComponent(id)}`),
    bench: (body) => api.post('/research/alpha/bench', body),
    benchStream: (jobId) => researchSse(`/alpha/bench/${jobId}/stream`),
    compareAlphas: (body) => api.post('/research/alpha/compare', body),
    compareStream: (jobId) => researchSse(`/alpha/compare/${jobId}/stream`),

    // Cross-asset maths
    correlation: (codes, days = 250, method = 'pearson') =>
        api.get('/research/correlation', { params: { codes, days, method }, timeout: 120000 }),
    regime: (codes, days = 365) =>
        api.get('/research/correlation/regime', { params: { codes, days }, timeout: 120000 }),
    payoff: (body) => api.post('/research/options/payoff', body),

    // Scheduled research
    playbooks: () => api.get('/research/scheduled-runs/playbooks'),
    playbook: (slug) => api.get(`/research/scheduled-runs/playbooks/${slug}`),
    scheduleFromPlaybook: (slug, body) => api.post(`/research/scheduled-runs/playbooks/${slug}`, body),
    schedules: () => api.get('/research/scheduled-runs'),
    schedule: (body) => api.post('/research/scheduled-runs', body),
    deleteSchedule: (id) => api.delete(`/research/scheduled-runs/${id}`),
    scheduleStatus: () => api.get('/research/scheduled-runs/status'),
}

// NSE/BSE option instruments from Tradeo's scrip master (no broker needed).
export const optionsApi = {
    underlyings: () => api.get('/options/underlyings'),
    expiries: (u) => api.get(`/options/expiries/${u}`),
    chain: (u, expiry) => api.get(`/options/chain/${u}${expiry ? `?expiry=${expiry}` : ''}`),
    spot: (u) => api.get(`/options/spot/${u}`, { timeout: 45000 }),
}

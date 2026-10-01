import { useState, useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
    TrendingUp, TrendingDown, Activity, Info,
    BarChart2, DollarSign, AlertTriangle, Plus,
    Brain, Shield, Users, MessageCircle, Star,
    BrainCircuit, FlaskConical, Grid3x3, Sigma
} from 'lucide-react'
import { stockApi, technicalsApi } from '../services/api'
import StockChart from '../components/StockChart'

function StockDetail() {
    const { symbol } = useParams()
    const [stock, setStock] = useState(null)
    const [price, setPrice] = useState(null)
    const [indicators, setIndicators] = useState(null)
    const [reversal, setReversal] = useState(null)
    const [fundamentals, setFundamentals] = useState(null)
    const [aiRec, setAiRec] = useState(null)
    const [quality, setQuality] = useState(null)
    const [sentiment, setSentiment] = useState(null)
    const [suitability, setSuitability] = useState(null)
    const [loading, setLoading] = useState(true)
    const [activeTab, setActiveTab] = useState('technical')

    useEffect(() => {
        if (symbol) loadStockData()
    }, [symbol])

    const loadStockData = async () => {
        setLoading(true)
        try {
            const [stockRes, priceRes] = await Promise.all([
                stockApi.getDetails(symbol),
                stockApi.getPrice(symbol),
            ])
            setStock(stockRes.data)
            setPrice(priceRes.data)

            // Load tab-specific data lazily
            loadTabData('technical')
        } catch (error) {
            console.error('Error loading stock:', error)
        }
        setLoading(false)
    }

    const loadTabData = async (tab) => {
        try {
            if (tab === 'technical' && !indicators) {
                const [indRes, revRes] = await Promise.all([
                    stockApi.getTechnicals(symbol),
                    stockApi.getReversal(symbol),
                ])
                setIndicators(indRes.data.indicators)
                setReversal(revRes.data)
            }
            if (tab === 'fundamental' && !fundamentals) {
                const res = await stockApi.getFundamentals(symbol)
                setFundamentals(res.data)
            }
            if (tab === 'ai' && !aiRec) {
                const [recRes, qualRes, suitRes] = await Promise.all([
                    stockApi.getAIRecommendation(symbol).catch(() => ({ data: null })),
                    stockApi.getQualityScore(symbol).catch(() => ({ data: null })),
                    stockApi.getSuitability(symbol).catch(() => ({ data: null })),
                ])
                setAiRec(recRes.data)
                setQuality(qualRes.data)
                setSuitability(suitRes.data)
            }
            if (tab === 'sentiment' && !sentiment) {
                const res = await stockApi.getSentiment(symbol).catch(() => ({ data: null }))
                setSentiment(res.data)
            }
        } catch (err) {
            console.error(`Error loading ${tab} data:`, err)
        }
    }

    const handleTabChange = (tab) => {
        setActiveTab(tab)
        loadTabData(tab)
    }

    const tabs = [
        { id: 'technical', label: 'Technical', icon: <Activity className="w-4 h-4" /> },
        { id: 'fundamental', label: 'Fundamental', icon: <BarChart2 className="w-4 h-4" /> },
        { id: 'sentiment', label: 'Sentiment', icon: <MessageCircle className="w-4 h-4" /> },
        { id: 'ai', label: 'AI Analysis', icon: <Brain className="w-4 h-4" /> },
        { id: 'peers', label: 'Peers', icon: <Users className="w-4 h-4" /> },
    ]

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-500" />
            </div>
        )
    }

    const changePercent = price?.change_percent || 0
    const isPositive = changePercent >= 0

    return (
        <div className="space-y-6 animate-fade-in">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h1 className="text-3xl font-bold text-white">{stock?.name || symbol}</h1>
                    <div className="flex items-center gap-3 mt-1">
                        <span className="text-dark-400">{symbol}</span>
                        <span className="text-dark-500">•</span>
                        <span className="text-dark-400">{stock?.sector || ''}</span>
                    </div>
                </div>
                <div className="text-right">
                    <div className="text-3xl font-bold text-white">₹{price?.price?.toLocaleString() || '—'}</div>
                    <div className={`flex items-center gap-1 justify-end ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
                        {isPositive ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
                        <span>₹{price?.change || 0} ({changePercent}%)</span>
                    </div>
                </div>
            </div>

            {/* Hand-offs to the research lab, pre-filled for this stock */}
            <ResearchActions symbol={symbol} />

            {/* Quick Stats Bar */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <QuickStat label="52W High" value={`₹${stock?.fifty_two_week_high?.toLocaleString() || '—'}`} />
                <QuickStat label="52W Low" value={`₹${stock?.fifty_two_week_low?.toLocaleString() || '—'}`} />
                <QuickStat label="Market Cap" value={formatMarketCap(stock?.market_cap)} />
                <QuickStat label="Volume" value={(price?.volume || 0).toLocaleString()} />
                <QuickStat label="P/E" value={stock?.pe_ratio || '—'} />
            </div>

            {/* AI Quick Badge */}
            {aiRec && (
                <div className={`glass-card p-4 flex items-center justify-between ${aiRec.recommendation.includes('BUY') ? 'ring-1 ring-green-500/30' :
                        aiRec.recommendation.includes('SELL') ? 'ring-1 ring-red-500/30' : 'ring-1 ring-amber-500/30'
                    }`}>
                    <div className="flex items-center gap-3">
                        <Brain className="w-5 h-5 text-purple-400" />
                        <span className="text-white font-medium">AI Recommendation:</span>
                        <span className={`font-bold text-lg ${aiRec.recommendation.includes('BUY') ? 'text-green-400' :
                                aiRec.recommendation.includes('SELL') ? 'text-red-400' : 'text-amber-400'
                            }`}>{aiRec.emoji} {aiRec.recommendation}</span>
                    </div>
                    <span className="text-dark-400">Confidence: {aiRec.confidence}%</span>
                </div>
            )}

            {/* Chart */}
            <div className="glass-card p-4">
                <StockChart symbol={symbol} />
            </div>

            {/* Tab Navigation */}
            <div className="flex gap-1 bg-dark-800 p-1 rounded-xl">
                {tabs.map(tab => (
                    <button key={tab.id} onClick={() => handleTabChange(tab.id)}
                        className={`flex items-center gap-2 px-4 py-2.5 rounded-lg flex-1 justify-center text-sm font-medium transition-all ${activeTab === tab.id
                                ? 'bg-primary-600 text-white shadow-lg'
                                : 'text-dark-400 hover:text-white hover:bg-dark-700'
                            }`}>
                        {tab.icon} {tab.label}
                    </button>
                ))}
            </div>

            {/* Tab Content */}
            <div className="glass-card p-6">
                {activeTab === 'technical' && <TechnicalTab indicators={indicators} reversal={reversal} />}
                {activeTab === 'fundamental' && <FundamentalTab fundamentals={fundamentals} />}
                {activeTab === 'sentiment' && <SentimentTab sentiment={sentiment} />}
                {activeTab === 'ai' && <AITab aiRec={aiRec} quality={quality} suitability={suitability} />}
                {activeTab === 'peers' && <PeersTab symbol={symbol} fundamentals={fundamentals} />}
            </div>
        </div>
    )
}

function QuickStat({ label, value }) {
    return (
        <div className="bg-dark-700/50 rounded-lg p-3 text-center">
            <div className="text-xs text-dark-500">{label}</div>
            <div className="text-white font-semibold mt-1">{value}</div>
        </div>
    )
}

function formatMarketCap(val) {
    if (!val) return '—'
    if (val >= 10000000000000) return `₹${(val / 10000000000000).toFixed(1)}T`
    if (val >= 100000000000) return `₹${(val / 10000000).toFixed(0)} Cr`
    if (val >= 10000000) return `₹${(val / 10000000).toFixed(1)} Cr`
    return `₹${val.toLocaleString()}`
}

// === TAB 1: TECHNICAL ===
function TechnicalTab({ indicators, reversal }) {
    if (!indicators) return <div className="text-dark-400">Loading technical data...</div>

    return (
        <div className="space-y-6">
            <h3 className="text-lg font-semibold text-white">Technical Indicators</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <IndicatorCard label="RSI (14)" value={indicators.rsi?.toFixed(1)}
                    signal={indicators.rsi < 30 ? '🟢 Oversold' : indicators.rsi > 70 ? '🔴 Overbought' : '🟡 Neutral'} />
                <IndicatorCard label="MACD" value={indicators.macd?.toFixed(4)} signal={indicators.macd_signal_type} />
                <IndicatorCard label="Trend" value={indicators.trend} />
                <IndicatorCard label="ADX" value={indicators.adx?.toFixed(1)} />
                <IndicatorCard label="SMA 20" value={`₹${indicators.sma_20?.toFixed(0) || '—'}`} />
                <IndicatorCard label="SMA 50" value={`₹${indicators.sma_50?.toFixed(0) || '—'}`} />
                <IndicatorCard label="SMA 200" value={`₹${indicators.sma_200?.toFixed(0) || '—'}`} />
                <IndicatorCard label="Volatility" value={indicators.volatility?.toFixed(2)} />
            </div>

            {reversal && (
                <>
                    <h3 className="text-lg font-semibold text-white mt-6">Reversal Analysis</h3>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                        <div className="bg-dark-700/50 rounded-lg p-4">
                            <div className="text-sm text-dark-400">Reversal Probability</div>
                            <div className={`text-2xl font-bold ${reversal.reversal_probability > 60 ? 'text-red-400' : reversal.reversal_probability > 30 ? 'text-amber-400' : 'text-green-400'}`}>
                                {reversal.reversal_probability}%
                            </div>
                        </div>
                        <div className="bg-dark-700/50 rounded-lg p-4">
                            <div className="text-sm text-dark-400">Direction</div>
                            <div className="text-xl font-bold text-white capitalize">{reversal.direction || 'neutral'}</div>
                        </div>
                        <div className="bg-dark-700/50 rounded-lg p-4">
                            <div className="text-sm text-dark-400">Recommendation</div>
                            <div className="text-xl font-bold text-primary-400">{reversal.recommendation || 'HOLD'}</div>
                        </div>
                    </div>
                    {reversal.signals?.length > 0 && (
                        <div className="space-y-2">
                            <h4 className="text-white font-medium">Active Signals</h4>
                            {reversal.signals.map((s, i) => (
                                <div key={i} className="flex items-center gap-2 text-sm p-2 bg-dark-700/30 rounded">
                                    <AlertTriangle className="w-4 h-4 text-amber-400" />
                                    <span className="text-dark-300">{s.message || s}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </>
            )}
        </div>
    )
}

function IndicatorCard({ label, value, signal }) {
    return (
        <div className="bg-dark-700/50 rounded-lg p-3">
            <div className="text-xs text-dark-500">{label}</div>
            <div className="text-lg font-bold text-white mt-1">{value || '—'}</div>
            {signal && <div className="text-xs text-dark-400 mt-1">{signal}</div>}
        </div>
    )
}

// === TAB 2: FUNDAMENTAL ===
function FundamentalTab({ fundamentals }) {
    if (!fundamentals) return <div className="text-dark-400">Loading fundamental data...</div>

    const sections = [
        {
            title: '📊 Valuation',
            metrics: [
                ['P/E Ratio', fundamentals.pe_ratio],
                ['Forward P/E', fundamentals.forward_pe],
                ['P/B Ratio', fundamentals.pb_ratio],
                ['P/S Ratio', fundamentals.ps_ratio],
                ['PEG Ratio', fundamentals.peg_ratio],
                ['EV/EBITDA', fundamentals.ev_to_ebitda],
            ]
        },
        {
            title: '💰 Profitability',
            metrics: [
                ['ROE', pct(fundamentals.roe)],
                ['ROA', pct(fundamentals.roa)],
                ['Gross Margin', pct(fundamentals.gross_margin)],
                ['Operating Margin', pct(fundamentals.operating_margin)],
                ['Net Margin', pct(fundamentals.profit_margin)],
                ['EPS', `₹${fundamentals.eps || '—'}`],
            ]
        },
        {
            title: '🏦 Financial Health',
            metrics: [
                ['Debt/Equity', fundamentals.debt_to_equity],
                ['Current Ratio', fundamentals.current_ratio],
                ['Quick Ratio', fundamentals.quick_ratio],
                ['Free Cash Flow', crore(fundamentals.free_cash_flow)],
                ['Total Cash', crore(fundamentals.total_cash)],
                ['Total Debt', crore(fundamentals.total_debt)],
            ]
        },
        {
            title: '📈 Growth',
            metrics: [
                ['Revenue Growth', pct(fundamentals.revenue_growth)],
                ['Earnings Growth', pct(fundamentals.earnings_growth)],
                ['Revenue', crore(fundamentals.revenue)],
                ['Net Income', crore(fundamentals.net_income)],
            ]
        },
        {
            title: '🎁 Dividends',
            metrics: [
                ['Dividend Yield', pct(fundamentals.dividend_yield)],
                ['Dividend Rate', `₹${fundamentals.dividend_rate || '—'}`],
                ['Payout Ratio', pct(fundamentals.payout_ratio)],
            ]
        },
    ]

    return (
        <div className="space-y-6">
            {sections.map(s => (
                <div key={s.title}>
                    <h3 className="text-white font-semibold mb-3">{s.title}</h3>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                        {s.metrics.map(([label, val]) => (
                            <div key={label} className="bg-dark-700/50 rounded-lg p-3 flex justify-between">
                                <span className="text-sm text-dark-400">{label}</span>
                                <span className="text-white font-medium">{val ?? '—'}</span>
                            </div>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    )
}

function pct(v) { return v != null ? `${(v * 100).toFixed(1)}%` : '—' }
function crore(v) {
    if (!v) return '—'
    const cr = v / 10000000
    if (Math.abs(cr) >= 100) return `₹${cr.toFixed(0)} Cr`
    return `₹${cr.toFixed(1)} Cr`
}

// === TAB 3: SENTIMENT ===
function SentimentTab({ sentiment }) {
    if (!sentiment) return <div className="text-dark-400">Loading sentiment data...</div>

    const score = sentiment.overall_sentiment ?? 0
    const label = score > 0.2 ? 'Bullish' : score < -0.2 ? 'Bearish' : 'Neutral'
    const color = score > 0.2 ? 'text-green-400' : score < -0.2 ? 'text-red-400' : 'text-amber-400'

    return (
        <div className="space-y-6">
            <div className="text-center py-6">
                <div className="text-sm text-dark-400 mb-2">Overall Sentiment</div>
                <div className={`text-5xl font-black ${color}`}>{label}</div>
                <div className="text-dark-500 mt-2">Score: {score.toFixed(2)} (range: -1 to +1)</div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <div className="bg-dark-700/50 rounded-lg p-4 text-center">
                    <div className="text-sm text-dark-400">Confidence</div>
                    <div className="text-2xl font-bold text-white">{((sentiment.confidence || 0.5) * 100).toFixed(0)}%</div>
                </div>
                <div className="bg-dark-700/50 rounded-lg p-4 text-center">
                    <div className="text-sm text-dark-400">Sources Analyzed</div>
                    <div className="text-2xl font-bold text-white">{sentiment.sources_count || 0}</div>
                </div>
                <div className="bg-dark-700/50 rounded-lg p-4 text-center">
                    <div className="text-sm text-dark-400">Mentions</div>
                    <div className="text-2xl font-bold text-white">{sentiment.mentions || 0}</div>
                </div>
            </div>

            {sentiment.headlines?.length > 0 && (
                <div>
                    <h3 className="text-white font-semibold mb-3">Recent Headlines</h3>
                    <div className="space-y-2">
                        {sentiment.headlines.map((h, i) => (
                            <div key={i} className="bg-dark-700/30 rounded-lg p-3 flex items-start gap-3">
                                <span className={`text-xs px-2 py-0.5 rounded mt-1 ${h.sentiment > 0 ? 'bg-green-500/20 text-green-400' :
                                        h.sentiment < 0 ? 'bg-red-500/20 text-red-400' : 'bg-gray-500/20 text-gray-400'
                                    }`}>
                                    {h.sentiment > 0 ? '+' : ''}{h.sentiment?.toFixed(2)}
                                </span>
                                <span className="text-dark-300 text-sm">{h.title}</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    )
}

// === TAB 4: AI ANALYSIS ===
function AITab({ aiRec, quality, suitability }) {
    return (
        <div className="space-y-6">
            {/* AI Recommendation */}
            {aiRec && (
                <div>
                    <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                        <Brain className="w-5 h-5 text-purple-400" /> AI Recommendation
                    </h3>
                    <div className={`rounded-xl p-6 text-center ${aiRec.recommendation.includes('BUY') ? 'bg-green-500/10 ring-1 ring-green-500/30' :
                            aiRec.recommendation.includes('SELL') ? 'bg-red-500/10 ring-1 ring-red-500/30' : 'bg-amber-500/10 ring-1 ring-amber-500/30'
                        }`}>
                        <div className="text-4xl mb-2">{aiRec.emoji}</div>
                        <div className={`text-3xl font-black ${aiRec.recommendation.includes('BUY') ? 'text-green-400' :
                                aiRec.recommendation.includes('SELL') ? 'text-red-400' : 'text-amber-400'
                            }`}>{aiRec.recommendation}</div>
                        <div className="text-dark-400 mt-2">Confidence: {aiRec.confidence}%</div>
                    </div>

                    <div className="grid grid-cols-3 gap-4 mt-4">
                        <ScoreBar label="Technical" score={aiRec.scores?.technical} />
                        <ScoreBar label="Fundamental" score={aiRec.scores?.fundamental} />
                        <ScoreBar label="Reversal Risk" score={aiRec.scores?.reversal_risk} inverted />
                    </div>

                    <div className="mt-4 grid grid-cols-3 gap-3 text-sm">
                        <div className="bg-dark-700/50 rounded p-2 text-center">
                            <div className="text-dark-500">RSI</div>
                            <div className="text-white font-medium">{aiRec.key_signals?.rsi?.toFixed(0) || '—'}</div>
                        </div>
                        <div className="bg-dark-700/50 rounded p-2 text-center">
                            <div className="text-dark-500">Trend</div>
                            <div className="text-white font-medium capitalize">{aiRec.key_signals?.trend}</div>
                        </div>
                        <div className="bg-dark-700/50 rounded p-2 text-center">
                            <div className="text-dark-500">MACD</div>
                            <div className="text-white font-medium capitalize">{aiRec.key_signals?.macd}</div>
                        </div>
                    </div>
                </div>
            )}

            {/* Quality Score */}
            {quality && (
                <div>
                    <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                        <Star className="w-5 h-5 text-amber-400" /> Business Quality
                    </h3>
                    <div className="flex items-center justify-between mb-4">
                        <div>
                            <span className="text-4xl font-black text-white">{quality.total_score}</span>
                            <span className="text-dark-400 text-xl">/100</span>
                        </div>
                        <span className={`text-2xl font-bold ${quality.grade === 'A+' || quality.grade === 'A' ? 'text-green-400' :
                                quality.grade === 'B+' || quality.grade === 'B' ? 'text-amber-400' : 'text-red-400'
                            }`}>{quality.grade}</span>
                    </div>
                    {quality.category_scores && (
                        <div className="space-y-2">
                            {Object.entries(quality.category_scores).map(([cat, score]) => (
                                <div key={cat} className="flex items-center gap-3">
                                    <span className="text-sm text-dark-400 w-32 capitalize">{cat.replace('_', ' ')}</span>
                                    <div className="flex-1 bg-dark-700 rounded-full h-3 overflow-hidden">
                                        <div className={`h-full rounded-full ${score >= 70 ? 'bg-green-500' : score >= 40 ? 'bg-amber-500' : 'bg-red-500'
                                            }`} style={{ width: `${score}%` }} />
                                    </div>
                                    <span className="text-sm text-white w-8 text-right">{score}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* Suitability */}
            {suitability && (
                <div>
                    <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                        <Shield className="w-5 h-5 text-cyan-400" /> Long-Term Suitability
                    </h3>
                    <div className={`rounded-xl p-4 text-center ${suitability.long_term_suitable ? 'bg-green-500/10' : 'bg-red-500/10'}`}>
                        <div className="text-3xl font-black text-white">{suitability.suitability_score}/100</div>
                        <div className={`text-sm mt-1 ${suitability.long_term_suitable ? 'text-green-400' : 'text-red-400'}`}>
                            {suitability.suitability_label}
                        </div>
                    </div>
                    {suitability.factors?.length > 0 && (
                        <div className="space-y-2 mt-4">
                            {suitability.factors.map((f, i) => (
                                <div key={i} className="text-sm text-dark-300 bg-dark-700/30 rounded p-2">{f}</div>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

function ScoreBar({ label, score, inverted }) {
    const val = score || 0
    const color = inverted
        ? (val < 30 ? 'bg-green-500' : val < 60 ? 'bg-amber-500' : 'bg-red-500')
        : (val >= 70 ? 'bg-green-500' : val >= 40 ? 'bg-amber-500' : 'bg-red-500')
    return (
        <div>
            <div className="flex justify-between text-sm mb-1">
                <span className="text-dark-400">{label}</span>
                <span className="text-white">{val.toFixed(0)}</span>
            </div>
            <div className="bg-dark-700 rounded-full h-2 overflow-hidden">
                <div className={`h-full rounded-full ${color}`} style={{ width: `${val}%` }} />
            </div>
        </div>
    )
}

// === TAB 5: PEERS ===
function PeersTab({ symbol, fundamentals }) {
    if (!fundamentals) return <div className="text-dark-400">Load fundamental data first to see peer comparison.</div>

    return (
        <div className="space-y-4">
            <h3 className="text-white font-semibold">Peer Comparison</h3>
            <p className="text-dark-400 text-sm">
                To compare {symbol} with peers, visit the <strong>Fundamental Analysis</strong> page which includes
                detailed peer comparison data from Screener.in.
            </p>
            <div className="grid grid-cols-2 gap-4">
                <div className="bg-dark-700/50 rounded-lg p-4">
                    <div className="text-sm text-dark-400">Sector</div>
                    <div className="text-white font-medium">{fundamentals.sector || '—'}</div>
                </div>
                <div className="bg-dark-700/50 rounded-lg p-4">
                    <div className="text-sm text-dark-400">Industry</div>
                    <div className="text-white font-medium">{fundamentals.industry || '—'}</div>
                </div>
            </div>
            <div className="bg-dark-700/30 rounded-xl p-6 text-center">
                <Users className="w-8 h-8 text-dark-500 mx-auto mb-2" />
                <p className="text-dark-400 text-sm">
                    Full peer comparison with industry averages is available through the
                    <code className="mx-1 px-2 py-0.5 bg-dark-700 rounded text-primary-400 text-xs">/api/fundamentals/{symbol}/peers</code>
                    endpoint.
                </p>
            </div>
        </div>
    )
}

export default StockDetail


function ResearchActions({ symbol }) {
    const q = (text) => `/research?q=${encodeURIComponent(text)}`
    const actions = [
        { to: q(`Research ${symbol}: use Tradeo's analysis, recent news and upcoming earnings, then give me the bull case, the bear case and what would change your view.`), icon: BrainCircuit, label: 'Deep research' },
        { to: q(`Backtest a trend-following strategy on ${symbol}.NS over the last 3 years with Indian delivery costs, and compare it with buy-and-hold.`), icon: FlaskConical, label: 'Backtest a strategy' },
        { to: '/research/options', icon: Sigma, label: 'Options lab' },
        { to: '/research/correlation', icon: Grid3x3, label: 'Correlation' },
    ]
    return (
        <div className="flex flex-wrap gap-2">
            {actions.map((a) => (
                <Link
                    key={a.label}
                    to={a.to}
                    className="press flex items-center gap-2 rounded-full border border-primary-400/20 bg-primary-500/5 px-3 py-1.5 text-xs text-primary-200 transition-all hover:-translate-y-0.5 hover:border-primary-400/50 hover:shadow-glow"
                >
                    <a.icon className="h-3.5 w-3.5" /> {a.label}
                </Link>
            ))}
        </div>
    )
}

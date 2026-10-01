import { useState, useEffect, useRef } from 'react'
import { Send, Bot, User, Sparkles, TrendingUp, BarChart2, Brain, Zap } from 'lucide-react'
import { chatApi } from '../services/api'

function Chat() {
    const [messages, setMessages] = useState([])
    const [input, setInput] = useState('')
    const [loading, setLoading] = useState(false)
    const messagesEndRef = useRef(null)

    useEffect(() => {
        loadHistory()
    }, [])

    useEffect(() => {
        scrollToBottom()
    }, [messages])

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }

    const loadHistory = async () => {
        try {
            const res = await chatApi.getHistory()
            setMessages(res.data.messages || [])
        } catch (error) {
            console.log('No chat history')
        }
    }

    const sendMessage = async (text = null) => {
        const msgText = text || input
        if (!msgText.trim() || loading) return

        const userMessage = { role: 'user', message: msgText, timestamp: new Date().toISOString() }
        setMessages(prev => [...prev, userMessage])
        setInput('')
        setLoading(true)

        try {
            const res = await chatApi.sendMessage(msgText)
            const assistantMessage = {
                role: 'assistant',
                message: res.data.response,
                data: res.data.data,
                follow_up_questions: res.data.follow_up_questions || [],
                timestamp: new Date().toISOString()
            }
            setMessages(prev => [...prev, assistantMessage])
        } catch (error) {
            console.error('Chat error:', error)
            setMessages(prev => [...prev, {
                role: 'assistant',
                message: 'Sorry, I encountered an error. Please try again.',
                timestamp: new Date().toISOString()
            }])
        }

        setLoading(false)
    }

    const handleKeyPress = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            sendMessage()
        }
    }

    const suggestedQuestions = [
        { icon: '📈', text: "What's the price of TCS?" },
        { icon: '💰', text: "Should I buy Reliance?" },
        { icon: '📊', text: "Analyze HDFC Bank technicals" },
        { icon: '💎', text: "Is TCS good for long-term?" },
        { icon: '😱', text: "What's the market mood?" },
        { icon: '🧬', text: "Match my personality to stocks" },
    ]

    const categoryButtons = [
        { label: 'Trading', icon: <Zap className="w-3.5 h-3.5" />, example: "Find breakout stocks in IT" },
        { label: 'Investing', icon: <TrendingUp className="w-3.5 h-3.5" />, example: "Quality stocks for long-term" },
        { label: 'Analysis', icon: <BarChart2 className="w-3.5 h-3.5" />, example: "Analyze Infosys fundamentals" },
        { label: 'AI Features', icon: <Brain className="w-3.5 h-3.5" />, example: "What's the market mood?" },
    ]

    return (
        <div className="flex flex-col h-[calc(100vh-8rem)] animate-fade-in">
            {/* Header */}
            <div className="flex items-center gap-3 mb-4">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-primary-500 to-purple-600 flex items-center justify-center">
                    <Sparkles className="w-6 h-6 text-white" />
                </div>
                <div>
                    <h1 className="text-2xl font-bold text-white">AI Trading Assistant</h1>
                    <p className="text-dark-400 text-sm">Powered by Llama 3.1 • Ask anything about Indian stocks</p>
                </div>
            </div>

            {/* Chat Container */}
            <div className="flex-1 glass-card flex flex-col overflow-hidden">
                {/* Messages */}
                <div className="flex-1 overflow-y-auto p-6 space-y-4">
                    {messages.length === 0 ? (
                        <div className="text-center py-8">
                            <Bot className="w-16 h-16 text-dark-600 mx-auto mb-4" />
                            <h3 className="text-lg font-semibold text-white mb-2">Hello! I'm your AI Assistant 🤖</h3>
                            <p className="text-dark-400 mb-6">I can analyze stocks, give recommendations, and help with trading decisions.</p>

                            {/* Category Buttons */}
                            <div className="flex flex-wrap justify-center gap-2 mb-6">
                                {categoryButtons.map((cat, i) => (
                                    <button key={i} onClick={() => sendMessage(cat.example)}
                                        className="flex items-center gap-2 px-4 py-2 bg-dark-700/80 hover:bg-dark-600 rounded-full text-sm text-dark-300 hover:text-white transition-all border border-dark-600/50 hover:border-primary-500/50">
                                        {cat.icon}
                                        <span>{cat.label}</span>
                                    </button>
                                ))}
                            </div>

                            {/* Suggested Questions */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-w-lg mx-auto">
                                {suggestedQuestions.map((q, i) => (
                                    <button key={i} onClick={() => sendMessage(q.text)}
                                        className="px-4 py-3 bg-dark-700/50 hover:bg-dark-600 rounded-xl text-sm text-dark-300 hover:text-white transition-all text-left border border-dark-700 hover:border-primary-500/30">
                                        <span className="mr-2">{q.icon}</span> {q.text}
                                    </button>
                                ))}
                            </div>
                        </div>
                    ) : (
                        messages.map((msg, i) => (
                            <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                                {msg.role === 'assistant' && (
                                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary-500 to-purple-600 flex items-center justify-center flex-shrink-0">
                                        <Bot className="w-4 h-4 text-white" />
                                    </div>
                                )}

                                <div className={`max-w-[75%] ${msg.role === 'user' ? '' : ''}`}>
                                    <div className={`rounded-2xl px-4 py-3 ${msg.role === 'user'
                                            ? 'bg-primary-600 text-white rounded-br-none'
                                            : 'bg-dark-700 text-white rounded-bl-none'
                                        }`}>
                                        <div className="whitespace-pre-wrap text-sm leading-relaxed"
                                            dangerouslySetInnerHTML={{
                                                __html: formatMessage(msg.message)
                                            }} />
                                    </div>

                                    {/* Data Card */}
                                    {msg.data && msg.role === 'assistant' && (
                                        <DataCard data={msg.data} />
                                    )}

                                    {/* Follow-up Questions */}
                                    {msg.follow_up_questions?.length > 0 && msg.role === 'assistant' && (
                                        <div className="flex flex-wrap gap-1.5 mt-2">
                                            {msg.follow_up_questions.map((q, qi) => (
                                                <button key={qi} onClick={() => sendMessage(q)}
                                                    className="text-xs px-3 py-1.5 bg-dark-700/80 text-primary-400 rounded-full hover:bg-dark-600 transition-all border border-dark-600/50 hover:border-primary-500/50">
                                                    {q}
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                {msg.role === 'user' && (
                                    <div className="w-8 h-8 rounded-full bg-dark-600 flex items-center justify-center flex-shrink-0">
                                        <User className="w-4 h-4 text-white" />
                                    </div>
                                )}
                            </div>
                        ))
                    )}

                    {loading && (
                        <div className="flex gap-3">
                            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary-500 to-purple-600 flex items-center justify-center">
                                <Bot className="w-4 h-4 text-white" />
                            </div>
                            <div className="bg-dark-700 rounded-2xl rounded-bl-none px-4 py-3">
                                <div className="flex gap-1">
                                    <span className="w-2 h-2 bg-dark-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                                    <span className="w-2 h-2 bg-dark-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                                    <span className="w-2 h-2 bg-dark-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                                </div>
                            </div>
                        </div>
                    )}

                    <div ref={messagesEndRef} />
                </div>

                {/* Input */}
                <div className="p-4 border-t border-dark-700">
                    <div className="flex gap-3">
                        <input
                            type="text"
                            className="input-field flex-1"
                            placeholder="Ask about stocks, get recommendations, analyze markets..."
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyPress={handleKeyPress}
                        />
                        <button onClick={() => sendMessage()} disabled={loading || !input.trim()}
                            className="btn-primary px-6 disabled:opacity-50">
                            <Send className="w-5 h-5" />
                        </button>
                    </div>
                </div>
            </div>
        </div>
    )
}

// Format markdown-like text to HTML
function formatMessage(text) {
    if (!text) return ''
    return text
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>')
        .replace(/- /g, '• ')
}

// Inline data card for price/indicators
function DataCard({ data }) {
    if (!data || typeof data !== 'object') return null

    // Price data
    if (data.price && data.change_percent !== undefined) {
        const isUp = data.change_percent >= 0
        return (
            <div className="mt-2 bg-dark-800 rounded-xl p-3 border border-dark-600/50">
                <div className="flex items-center justify-between">
                    <span className="text-white font-bold text-lg">₹{data.price?.toLocaleString()}</span>
                    <span className={`text-sm font-medium ${isUp ? 'text-green-400' : 'text-red-400'}`}>
                        {isUp ? '▲' : '▼'} {data.change_percent}%
                    </span>
                </div>
                {data.volume && (
                    <span className="text-xs text-dark-500">Vol: {data.volume?.toLocaleString()}</span>
                )}
            </div>
        )
    }

    // Quality data
    if (data.quality && data.quality.total_score) {
        return (
            <div className="mt-2 bg-dark-800 rounded-xl p-3 border border-dark-600/50">
                <div className="flex items-center justify-between">
                    <span className="text-dark-400 text-sm">Quality Score</span>
                    <span className="text-white font-bold">{data.quality.total_score}/100 ({data.quality.grade})</span>
                </div>
            </div>
        )
    }

    return null
}

export default Chat

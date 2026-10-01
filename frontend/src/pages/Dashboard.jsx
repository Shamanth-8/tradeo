import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    TrendingUp,
    TrendingDown,
    Activity,
    Wallet,
    Target,
    ArrowUpRight,
    ArrowDownRight,
    RefreshCw
} from 'lucide-react'
import { stockApi, portfolioApi, paperTradingApi } from '../services/api'

// Sample stocks for dashboard
const SAMPLE_STOCKS = ['TCS', 'RELIANCE', 'HDFCBANK', 'INFY', 'ICICIBANK']

function Dashboard() {
    const navigate = useNavigate()
    const [marketData, setMarketData] = useState([])
    const [portfolioPerformance, setPortfolioPerformance] = useState(null)
    const [paperAccount, setPaperAccount] = useState(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        loadDashboardData()
    }, [])

    const loadDashboardData = async () => {
        setLoading(true)
        try {
            // Load stock prices
            const stockPromises = SAMPLE_STOCKS.map(symbol =>
                stockApi.getPrice(symbol).then(res => res.data).catch(() => null)
            )
            const stocks = await Promise.all(stockPromises)
            setMarketData(stocks.filter(Boolean))

            // Load portfolio performance
            try {
                const perfResponse = await portfolioApi.getPerformance()
                setPortfolioPerformance(perfResponse.data)
            } catch (e) {
                console.log('No portfolio data')
            }

            // Load paper account
            try {
                const accountResponse = await paperTradingApi.getAccount()
                setPaperAccount(accountResponse.data)
            } catch (e) {
                console.log('No paper account')
            }
        } catch (error) {
            console.error('Dashboard load error:', error)
        }
        setLoading(false)
    }

    const StatCard = ({ title, value, change, changePercent, icon: Icon, color }) => (
        <div className="stat-card">
            <div className="flex items-start justify-between">
                <div>
                    <p className="text-sm text-dark-400 mb-1">{title}</p>
                    <p className="text-2xl font-bold text-white">{value}</p>
                    {change !== undefined && (
                        <div className={`flex items-center gap-1 mt-2 ${changePercent >= 0 ? 'text-success-400' : 'text-danger-400'}`}>
                            {changePercent >= 0 ? <ArrowUpRight className="w-4 h-4" /> : <ArrowDownRight className="w-4 h-4" />}
                            <span className="text-sm font-medium">
                                ₹{Math.abs(change).toLocaleString()} ({changePercent >= 0 ? '+' : ''}{changePercent}%)
                            </span>
                        </div>
                    )}
                </div>
                <div className={`p-3 rounded-xl ${color}`}>
                    <Icon className="w-6 h-6 text-white" />
                </div>
            </div>
        </div>
    )

    return (
        <div className="space-y-6 animate-fade-in">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white">Dashboard</h1>
                    <p className="text-dark-400">Welcome back! Here's your market overview.</p>
                </div>
                <button
                    onClick={loadDashboardData}
                    className="btn-secondary flex items-center gap-2"
                >
                    <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    Refresh
                </button>
            </div>

            {/* Stats Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard
                    title="Portfolio Value"
                    value={`₹${portfolioPerformance?.total_current_value?.toLocaleString() || '0'}`}
                    change={portfolioPerformance?.total_pnl || 0}
                    changePercent={portfolioPerformance?.total_pnl_percent || 0}
                    icon={Wallet}
                    color="bg-gradient-to-br from-primary-500 to-primary-700"
                />
                <StatCard
                    title="Paper Trading"
                    value={`₹${paperAccount?.total_value?.toLocaleString() || '10,00,000'}`}
                    change={paperAccount?.total_pnl || 0}
                    changePercent={paperAccount?.total_pnl ? ((paperAccount.total_pnl / paperAccount.starting_capital) * 100).toFixed(2) : 0}
                    icon={Target}
                    color="bg-gradient-to-br from-purple-500 to-purple-700"
                />
                <StatCard
                    title="Short-term P&L"
                    value={`₹${portfolioPerformance?.short_term?.pnl?.toLocaleString() || '0'}`}
                    icon={TrendingUp}
                    color="bg-gradient-to-br from-success-500 to-success-700"
                />
                <StatCard
                    title="Long-term P&L"
                    value={`₹${portfolioPerformance?.long_term?.pnl?.toLocaleString() || '0'}`}
                    icon={Activity}
                    color="bg-gradient-to-br from-orange-500 to-orange-700"
                />
            </div>

            {/* Market Overview */}
            <div className="glass-card p-6">
                <h2 className="text-lg font-semibold text-white mb-4">Market Overview</h2>
                {loading ? (
                    <div className="flex items-center justify-center py-8">
                        <RefreshCw className="w-8 h-8 animate-spin text-primary-500" />
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
                        {marketData.map((stock) => (
                            <button
                                key={stock.symbol}
                                onClick={() => navigate(`/stock/${stock.symbol}`)}
                                className="p-4 bg-dark-700/50 rounded-xl hover:bg-dark-700 transition-all hover:scale-[1.02] text-left"
                            >
                                <div className="flex items-center justify-between mb-2">
                                    <span className="font-bold text-white">{stock.symbol}</span>
                                    {stock.change_percent >= 0 ? (
                                        <TrendingUp className="w-4 h-4 text-success-400" />
                                    ) : (
                                        <TrendingDown className="w-4 h-4 text-danger-400" />
                                    )}
                                </div>
                                <p className="text-xl font-bold text-white">₹{stock.price?.toLocaleString()}</p>
                                <p className={`text-sm ${stock.change_percent >= 0 ? 'text-success-400' : 'text-danger-400'}`}>
                                    {stock.change_percent >= 0 ? '+' : ''}{stock.change_percent}%
                                </p>
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {/* Quick Actions */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <button
                    onClick={() => navigate('/portfolio')}
                    className="glass-card p-6 hover:border-primary-500/50 transition-all group"
                >
                    <Wallet className="w-8 h-8 text-primary-400 mb-3 group-hover:scale-110 transition-transform" />
                    <h3 className="font-semibold text-white mb-1">View Portfolio</h3>
                    <p className="text-sm text-dark-400">Track your real investments</p>
                </button>

                <button
                    onClick={() => navigate('/paper-trading')}
                    className="glass-card p-6 hover:border-purple-500/50 transition-all group"
                >
                    <Target className="w-8 h-8 text-purple-400 mb-3 group-hover:scale-110 transition-transform" />
                    <h3 className="font-semibold text-white mb-1">Paper Trading</h3>
                    <p className="text-sm text-dark-400">Practice with virtual money</p>
                </button>

                <button
                    onClick={() => navigate('/chat')}
                    className="glass-card p-6 hover:border-success-500/50 transition-all group"
                >
                    <Activity className="w-8 h-8 text-success-400 mb-3 group-hover:scale-110 transition-transform" />
                    <h3 className="font-semibold text-white mb-1">AI Assistant</h3>
                    <p className="text-sm text-dark-400">Get AI-powered insights</p>
                </button>
            </div>
        </div>
    )
}

export default Dashboard

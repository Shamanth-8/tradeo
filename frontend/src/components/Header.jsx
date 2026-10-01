import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, Bell, Settings, User } from 'lucide-react'
import { useAppStore } from '../store'
import { stockApi } from '../services/api'

function Header() {
    const [searchQuery, setSearchQuery] = useState('')
    const [searchResults, setSearchResults] = useState([])
    const [showResults, setShowResults] = useState(false)
    const navigate = useNavigate()

    const handleSearch = async (e) => {
        const query = e.target.value
        setSearchQuery(query)

        if (query.length >= 2) {
            try {
                const response = await stockApi.search(query)
                setSearchResults(response.data.results || [])
                setShowResults(true)
            } catch (error) {
                console.error('Search error:', error)
            }
        } else {
            setSearchResults([])
            setShowResults(false)
        }
    }

    const handleSelectStock = (symbol) => {
        navigate(`/stock/${symbol}`)
        setSearchQuery('')
        setShowResults(false)
    }

    return (
        <header className="h-16 bg-dark-800 border-b border-dark-700 px-6 flex items-center justify-between">
            {/* Search Bar */}
            <div className="relative w-96">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-dark-400" />
                <input
                    type="text"
                    placeholder="Search stocks... (e.g., TCS, Reliance)"
                    value={searchQuery}
                    onChange={handleSearch}
                    onFocus={() => searchResults.length > 0 && setShowResults(true)}
                    onBlur={() => setTimeout(() => setShowResults(false), 200)}
                    className="input-field pl-10"
                />

                {/* Search Results Dropdown */}
                {showResults && searchResults.length > 0 && (
                    <div className="absolute top-full left-0 right-0 mt-2 bg-dark-800 border border-dark-600 rounded-lg shadow-xl z-50 overflow-hidden">
                        {searchResults.map((stock) => (
                            <button
                                key={stock.symbol}
                                onClick={() => handleSelectStock(stock.symbol)}
                                className="w-full px-4 py-3 text-left hover:bg-dark-700 transition-colors flex items-center justify-between"
                            >
                                <div>
                                    <p className="font-medium text-white">{stock.symbol}</p>
                                    <p className="text-sm text-dark-400">{stock.name}</p>
                                </div>
                                <span className="text-xs text-dark-500">{stock.exchange}</span>
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {/* Right Side */}
            <div className="flex items-center gap-4">
                {/* Strategy Toggle */}
                <div className="flex items-center gap-2 bg-dark-700 rounded-lg p-1">
                    <button className="px-3 py-1.5 text-sm font-medium rounded-md bg-primary-600 text-white">
                        All
                    </button>
                    <button className="px-3 py-1.5 text-sm font-medium rounded-md text-dark-400 hover:text-white">
                        Short-term
                    </button>
                    <button className="px-3 py-1.5 text-sm font-medium rounded-md text-dark-400 hover:text-white">
                        Long-term
                    </button>
                </div>

                {/* Icons */}
                <button className="p-2 text-dark-400 hover:text-white hover:bg-dark-700 rounded-lg transition-colors relative">
                    <Bell className="w-5 h-5" />
                    <span className="absolute top-1 right-1 w-2 h-2 bg-danger-500 rounded-full"></span>
                </button>

                <button className="p-2 text-dark-400 hover:text-white hover:bg-dark-700 rounded-lg transition-colors">
                    <Settings className="w-5 h-5" />
                </button>

                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
                    <User className="w-4 h-4 text-white" />
                </div>
            </div>
        </header>
    )
}

export default Header

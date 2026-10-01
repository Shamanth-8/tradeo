import { create } from 'zustand'

// App Store - Global state management
export const useAppStore = create((set) => ({
    // Theme
    darkMode: true,
    toggleDarkMode: () => set((state) => ({ darkMode: !state.darkMode })),

    // Search
    searchQuery: '',
    setSearchQuery: (query) => set({ searchQuery: query }),

    // Selected Strategy Filter
    strategyFilter: 'all', // 'all', 'short-term', 'long-term'
    setStrategyFilter: (filter) => set({ strategyFilter: filter }),

    // Loading States
    isLoading: false,
    setLoading: (loading) => set({ isLoading: loading }),

    // Notifications
    notifications: [],
    addNotification: (notification) => set((state) => ({
        notifications: [...state.notifications, { id: Date.now(), ...notification }]
    })),
    removeNotification: (id) => set((state) => ({
        notifications: state.notifications.filter(n => n.id !== id)
    })),
}))

// Portfolio Store
export const usePortfolioStore = create((set) => ({
    holdings: [],
    shortTermHoldings: [],
    longTermHoldings: [],
    performance: null,

    setHoldings: (holdings) => set({ holdings }),
    setShortTermHoldings: (holdings) => set({ shortTermHoldings: holdings }),
    setLongTermHoldings: (holdings) => set({ longTermHoldings: holdings }),
    setPerformance: (performance) => set({ performance }),
}))

// Paper Trading Store
export const usePaperTradingStore = create((set) => ({
    account: null,
    portfolio: [],
    history: [],
    performance: null,

    setAccount: (account) => set({ account }),
    setPortfolio: (portfolio) => set({ portfolio }),
    setHistory: (history) => set({ history }),
    setPerformance: (performance) => set({ performance }),
}))

// Chat Store
export const useChatStore = create((set) => ({
    messages: [],
    isTyping: false,

    addMessage: (message) => set((state) => ({
        messages: [...state.messages, message]
    })),
    setMessages: (messages) => set({ messages }),
    setTyping: (isTyping) => set({ isTyping }),
}))

// Watchlist Store
export const useWatchlistStore = create((set) => ({
    watchlist: [],

    addToWatchlist: (stock) => set((state) => ({
        watchlist: [...state.watchlist.filter(s => s.symbol !== stock.symbol), stock]
    })),
    removeFromWatchlist: (symbol) => set((state) => ({
        watchlist: state.watchlist.filter(s => s.symbol !== symbol)
    })),
    setWatchlist: (watchlist) => set({ watchlist }),
}))

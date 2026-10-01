import { Suspense, lazy } from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import ErrorBoundary from './components/ErrorBoundary'

// Command surface
import Command from './pages/Command'
import Wealth from './pages/Wealth'
import Signals from './pages/Signals'

// Lazily loaded: the Analyst is the only screen that pulls in recharts, which
// is ~450 kB minified. Code-splitting it keeps that entirely off the first
// paint for anyone who never opens the analyst.
const Analyst = lazy(() => import('./pages/Analyst'))

// Also lazy, and for the same reason: the studio pulls in recharts *and* the
// code editor, and neither belongs in the bundle of someone who only ever
// opens the command deck.
const Studio = lazy(() => import('./pages/Studio'))

// The research lab (the merged research engine). All lazy: several pull in
// recharts, and none belong in the first paint of the command deck.
const RESEARCH_ROUTES = [
    ['research', lazy(() => import('./pages/Research'))],
    ['research/swarm', lazy(() => import('./pages/Swarm'))],
    ['research/alpha', lazy(() => import('./pages/AlphaZoo'))],
    ['research/options', lazy(() => import('./pages/OptionsLab'))],
    ['research/correlation', lazy(() => import('./pages/Correlation'))],
    ['research/schedules', lazy(() => import('./pages/Schedules'))],
    ['research/runs', lazy(() => import('./pages/ResearchRuns'))],
    ['research/runs/:runId', lazy(() => import('./pages/ResearchRuns'))],
]

import Discover from './pages/Discover'
import Learn from './pages/Learn'
import Autopilot from './pages/Autopilot'
import Setup from './pages/Setup'
import Labs from './pages/Labs'
import LongTerm from './pages/LongTerm'

// Retained from v2
import StockDetail from './pages/StockDetail'
import Portfolio from './pages/Portfolio'
import PaperTrading from './pages/PaperTrading'
import Alerts from './pages/Alerts'
import Chat from './pages/Chat'

// Novel AI features
import TradeClone from './pages/novel/TradeClone'
import RegretAnalyzer from './pages/novel/RegretAnalyzer'
import MoodRing from './pages/novel/MoodRing'
import DNAMatching from './pages/novel/DNAMatching'
import FutureYou from './pages/novel/FutureYou'
import MarginOfSafety from './pages/novel/MarginOfSafety'
import ExitArchitect from './pages/novel/ExitArchitect'

/**
 * HashRouter rather than BrowserRouter: the app also ships as an Electron
 * build loading from file://, where path-based routing has no server to
 * resolve deep links against.
 */
export default function App() {
    return (
        <HashRouter>
            <Routes>
                {/* One boundary around every route: a component that throws
                    should cost you that screen, not the application. */}
                <Route
                    path="/"
                    element={
                        <ErrorBoundary label="This screen failed">
                            <Layout />
                        </ErrorBoundary>
                    }
                >
                    <Route index element={<Command />} />
                    <Route path="wealth" element={<Wealth />} />
                    <Route path="signals" element={<Signals />} />
                    <Route
                        path="analyst"
                        element={
                            <Suspense
                                fallback={
                                    <div className="p-8 text-sm text-dark-500">
                                        Loading analyst…
                                    </div>
                                }
                            >
                                <Analyst />
                            </Suspense>
                        }
                    />
                    <Route
                        path="studio"
                        element={
                            <Suspense
                                fallback={
                                    <div className="p-8 text-sm text-dark-500">
                                        Loading studio…
                                    </div>
                                }
                            >
                                <Studio />
                            </Suspense>
                        }
                    />
                    {RESEARCH_ROUTES.map(([path, Page]) => (
                        <Route
                            key={path}
                            path={path}
                            element={
                                <Suspense fallback={<div className="p-8 text-sm text-dark-500">Loading research lab…</div>}>
                                    <Page />
                                </Suspense>
                            }
                        />
                    ))}
                    <Route path="discover" element={<Discover />} />
                    <Route path="learn" element={<Learn />} />
                    <Route path="autopilot" element={<Autopilot />} />
                    <Route path="setup" element={<Setup />} />
                    <Route path="labs" element={<Labs />} />
                    <Route path="long-term" element={<LongTerm />} />

                    <Route path="stock/:symbol" element={<StockDetail />} />
                    <Route path="portfolio" element={<Portfolio />} />
                    <Route path="paper-trading" element={<PaperTrading />} />
                    {/* The old standalone backtest screen is now the Backtest
                        tab inside the studio. Kept as a redirect so existing
                        links and bookmarks land somewhere useful. */}
                    <Route path="backtest" element={<Navigate to="/studio" replace />} />
                    <Route path="alerts" element={<Alerts />} />
                    <Route path="chat" element={<Chat />} />

                    <Route path="novel/trade-clone" element={<TradeClone />} />
                    <Route path="novel/regret" element={<RegretAnalyzer />} />
                    <Route path="novel/mood" element={<MoodRing />} />
                    <Route path="novel/dna" element={<DNAMatching />} />
                    <Route path="novel/future" element={<FutureYou />} />
                    <Route path="novel/margin" element={<MarginOfSafety />} />
                    <Route path="novel/exit" element={<ExitArchitect />} />
                </Route>
            </Routes>
        </HashRouter>
    )
}

"""
SQLite Database Schema and Initialization
"""

import sqlite3
from pathlib import Path
import os

DATABASE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "database", "trading.db"
)


def get_db_connection():
    """Get a connection to the SQLite database (creating its folder on a fresh clone)."""
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database with all required tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Stocks Master Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sector TEXT,
            industry TEXT,
            market_cap REAL,
            exchange TEXT
        )
    """)

    # Historical Stock Prices
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol),
            UNIQUE(symbol, date)
        )
    """)

    # Technical Indicators
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS technical_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            rsi REAL,
            macd REAL,
            macd_signal REAL,
            macd_histogram REAL,
            bb_upper REAL,
            bb_middle REAL,
            bb_lower REAL,
            sma_20 REAL,
            sma_50 REAL,
            ema_12 REAL,
            ema_26 REAL,
            atr REAL,
            obv INTEGER,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol),
            UNIQUE(symbol, date)
        )
    """)

    # Fundamental Data
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            quarter TEXT,
            pe_ratio REAL,
            pb_ratio REAL,
            debt_to_equity REAL,
            roe REAL,
            eps REAL,
            revenue REAL,
            net_profit REAL,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol),
            UNIQUE(symbol, quarter)
        )
    """)

    # Sentiment Data
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sentiment_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            source TEXT,
            sentiment_score REAL,
            volume INTEGER,
            text TEXT,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # News & Social Feed
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news_feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT,
            source TEXT NOT NULL,
            url TEXT UNIQUE,
            published_at DATETIME,
            sentiment_score REAL,
            related_symbols TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # AI Predictions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            prediction_date DATE,
            predicted_direction TEXT,
            reversal_probability REAL,
            predicted_price_target REAL,
            confidence REAL,
            model_version TEXT,
            actual_outcome TEXT,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Manual Portfolio
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            avg_buy_price REAL NOT NULL,
            first_buy_date DATE NOT NULL,
            investment_strategy TEXT NOT NULL DEFAULT 'short-term',
            investment_thesis TEXT,
            target_price REAL,
            stop_loss REAL,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Portfolio Transactions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            transaction_date DATE NOT NULL,
            investment_strategy TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Paper Trading Portfolio
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            avg_buy_price REAL NOT NULL,
            first_buy_date DATE NOT NULL,
            investment_strategy TEXT NOT NULL DEFAULT 'short-term',
            target_price REAL,
            stop_loss REAL,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Paper Trading Transactions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            trade_type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            trade_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            investment_strategy TEXT NOT NULL,
            pnl REAL,
            holding_days INTEGER,
            notes TEXT,
            tags TEXT,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Paper Trading Account Summary
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            starting_capital REAL NOT NULL,
            current_cash REAL NOT NULL,
            total_pnl REAL,
            total_trades INTEGER,
            winning_trades INTEGER,
            losing_trades INTEGER,
            last_reset DATE
        )
    """)

    # Backtesting Results
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name TEXT NOT NULL,
            symbol TEXT,
            start_date DATE,
            end_date DATE,
            total_return REAL,
            annual_return REAL,
            win_rate REAL,
            profit_factor REAL,
            max_drawdown REAL,
            sharpe_ratio REAL,
            total_trades INTEGER,
            parameters TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Alerts
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            condition TEXT NOT NULL,
            threshold REAL,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            triggered_at DATETIME,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Chat History
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            message TEXT NOT NULL,
            role TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # App Settings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # User Preferences
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            default_strategy TEXT DEFAULT 'short-term',
            short_term_risk_tolerance TEXT DEFAULT 'medium',
            short_term_target_return REAL DEFAULT 15.0,
            long_term_risk_tolerance TEXT DEFAULT 'medium',
            long_term_min_roe REAL DEFAULT 15.0,
            long_term_max_debt_equity REAL DEFAULT 0.5,
            long_term_min_revenue_growth REAL DEFAULT 10.0,
            preferred_sectors TEXT,
            avoid_sectors TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ===== NOVEL FEATURES TABLES =====

    # Extended Fundamental Data (beyond yfinance basics)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals_extended (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            fetch_date DATE DEFAULT CURRENT_DATE,
            roic REAL,
            interest_coverage REAL,
            book_value_per_share REAL,
            revenue_5yr_cagr REAL,
            earnings_5yr_cagr REAL,
            book_value_growth REAL,
            dividend_growth_5yr REAL,
            promoter_holding REAL,
            promoter_pledging REAL,
            roce REAL,
            cash_conversion_cycle REAL,
            source TEXT DEFAULT 'screener',
            raw_data TEXT,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol),
            UNIQUE(symbol, fetch_date)
        )
    """)

    # Economic Indicators (Indian Market)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS economic_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_name TEXT NOT NULL,
            value REAL,
            previous_value REAL,
            change_pct REAL,
            date DATE DEFAULT CURRENT_DATE,
            source TEXT,
            UNIQUE(indicator_name, date)
        )
    """)

    # Trade Clone: Tracked Portfolios
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracked_portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_name TEXT NOT NULL UNIQUE,
            investor_type TEXT,
            source TEXT,
            description TEXT,
            tracked_since DATE DEFAULT CURRENT_DATE,
            total_return REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            avg_holding_period INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Trade Clone: Tracked Holdings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracked_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            buy_date DATE,
            buy_price REAL,
            sell_date DATE,
            sell_price REAL,
            quantity INTEGER,
            return_pct REAL,
            sector TEXT,
            FOREIGN KEY (portfolio_id) REFERENCES tracked_portfolios(id),
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Trade Regret Analysis
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS regret_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_trade_id INTEGER,
            symbol TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            decision_date DATE,
            actual_price REAL,
            actual_return_pct REAL,
            optimal_exit_price REAL,
            optimal_exit_date DATE,
            optimal_return_pct REAL,
            hold_until_now_price REAL,
            hold_until_now_return_pct REAL,
            regret_score REAL,
            lesson TEXT,
            pattern TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Market Mood History
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_mood_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            mood TEXT NOT NULL,
            fear_greed_index REAL,
            india_vix REAL,
            market_breadth REAL,
            fii_net_flow REAL,
            dii_net_flow REAL,
            news_sentiment REAL,
            social_sentiment REAL,
            nifty_change_pct REAL
        )
    """)

    # Stock DNA Profiles
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_dna_profiles (
            symbol TEXT PRIMARY KEY,
            volatility_score INTEGER DEFAULT 5,
            growth_score INTEGER DEFAULT 5,
            stability_score INTEGER DEFAULT 5,
            dividend_score INTEGER DEFAULT 5,
            quality_score INTEGER DEFAULT 5,
            momentum_score INTEGER DEFAULT 5,
            personality_type TEXT,
            suitable_for TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Investor DNA Profile
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS investor_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            check_frequency TEXT,
            reaction_to_dip TEXT,
            sleep_concern TEXT,
            investment_horizon TEXT,
            income_vs_growth TEXT,
            risk_score INTEGER DEFAULT 50,
            personality_type TEXT DEFAULT 'balanced',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Future You Simulations
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS future_simulations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            simulation_date DATE DEFAULT CURRENT_DATE,
            years_forward INTEGER NOT NULL,
            starting_capital REAL NOT NULL,
            monthly_sip REAL DEFAULT 0,
            expected_annual_return REAL,
            pessimistic_outcome REAL,
            realistic_outcome REAL,
            optimistic_outcome REAL,
            median_outcome REAL,
            probability_of_loss REAL,
            simulation_runs INTEGER DEFAULT 10000,
            portfolio_symbols TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Margin of Safety Calculations
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS margin_of_safety (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            calculation_date DATE DEFAULT CURRENT_DATE,
            current_price REAL,
            intrinsic_value_dcf REAL,
            intrinsic_value_graham REAL,
            intrinsic_value_earnings REAL,
            avg_intrinsic_value REAL,
            margin_of_safety_pct REAL,
            risk_score INTEGER,
            verdict TEXT,
            assumptions TEXT,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol),
            UNIQUE(symbol, calculation_date)
        )
    """)

    # Exit Strategies
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exit_strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            trigger_condition TEXT NOT NULL,
            target_value REAL,
            current_status TEXT DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            triggered_at DATETIME,
            notes TEXT,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        )
    """)

    # Initialize paper account with default capital
    cursor.execute("""
        INSERT OR IGNORE INTO paper_account (id, starting_capital, current_cash, total_pnl, total_trades, winning_trades, losing_trades)
        VALUES (1, 1000000, 1000000, 0, 0, 0, 0)
    """)

    # Initialize user preferences
    cursor.execute("""
        INSERT OR IGNORE INTO user_preferences (id) VALUES (1)
    """)

    conn.commit()
    conn.close()
    print("Database initialized successfully!")


if __name__ == "__main__":
    init_db()

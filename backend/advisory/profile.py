"""
Risk profiling.

Two things get measured separately, because they are genuinely different and
conflating them is how people end up mis-sold products:

  CAPACITY  — how much loss your circumstances can absorb. Objective: horizon,
              income stability, emergency buffer, dependents, what share of
              your wealth this is.
  TOLERANCE — how much loss you can sit through without selling. Subjective:
              what you'd actually do in a 30% drawdown, and how well you
              understand what you own.

The binding profile is the *lower* of the two. Someone with a 30-year horizon
and no dependents has high capacity, but if they'd panic-sell at −20% then an
aggressive allocation will still lose them money — they'll realise the loss at
the bottom. Equally, high tolerance doesn't help if the money is needed in
eighteen months.

This is a suitability tool, not a substitute for a registered adviser.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from data.storage.database import get_db_connection

log = logging.getLogger("tradeo.profile")

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    profile TEXT NOT NULL,
    capacity_score REAL,
    tolerance_score REAL,
    combined_score REAL,
    horizon_years REAL,
    answers TEXT,
    target_allocation TEXT,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""


def _init() -> None:
    conn = get_db_connection()
    try:
        conn.execute(SCHEMA)
        conn.commit()
    finally:
        conn.close()


_init()


# --- Questionnaire ----------------------------------------------------------
# Each option carries points 0-100 on the dimension it measures.

QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "horizon",
        "dimension": "capacity",
        "weight": 2.0,
        "question": "When will you need this money back?",
        "help": "The single biggest driver of how much risk you can take. Equity needs time to recover from bad years.",
        "options": [
            {"label": "Within 1 year", "score": 0, "meta": {"years": 1}},
            {"label": "1-3 years", "score": 25, "meta": {"years": 2}},
            {"label": "3-7 years", "score": 55, "meta": {"years": 5}},
            {"label": "7-15 years", "score": 80, "meta": {"years": 10}},
            {"label": "15+ years, or never — this is long-term wealth", "score": 100, "meta": {"years": 20}},
        ],
    },
    {
        "id": "income_stability",
        "dimension": "capacity",
        "weight": 1.5,
        "question": "How predictable is your income?",
        "help": "A stable salary lets you keep investing through a downturn instead of selling into it.",
        "options": [
            {"label": "Irregular — freelance, business, commission", "score": 20},
            {"label": "Mostly stable with variable components", "score": 55},
            {"label": "Fixed salary, secure employment", "score": 85},
            {"label": "Multiple stable income sources", "score": 100},
        ],
    },
    {
        "id": "emergency_fund",
        "dimension": "capacity",
        "weight": 1.5,
        "question": "How many months of expenses do you hold in cash or liquid funds?",
        "help": "Without a buffer, the first emergency forces you to sell investments at whatever price the market offers that day.",
        "options": [
            {"label": "None", "score": 0},
            {"label": "1-3 months", "score": 35},
            {"label": "3-6 months", "score": 75},
            {"label": "More than 6 months", "score": 100},
        ],
    },
    {
        "id": "dependents",
        "dimension": "capacity",
        "weight": 1.0,
        "question": "How many people depend on your income?",
        "options": [
            {"label": "Only me", "score": 100},
            {"label": "One other person", "score": 70},
            {"label": "Two or three", "score": 45},
            {"label": "Four or more", "score": 25},
        ],
    },
    {
        "id": "wealth_share",
        "dimension": "capacity",
        "weight": 1.5,
        "question": "What share of your total savings is this portfolio?",
        "help": "Risking 10% of your wealth is a different decision from risking all of it.",
        "options": [
            {"label": "Almost all of it", "score": 20},
            {"label": "More than half", "score": 45},
            {"label": "Roughly a quarter to a half", "score": 75},
            {"label": "A small share — most of my wealth is elsewhere", "score": 100},
        ],
    },
    {
        "id": "drawdown_reaction",
        "dimension": "tolerance",
        "weight": 2.5,
        "question": "Your portfolio falls 30% in three months. What do you actually do?",
        "help": "Be honest. This is the question that decides whether a strategy survives contact with a real bear market.",
        "options": [
            {"label": "Sell everything — I can't sleep through that", "score": 0},
            {"label": "Sell some to stop the bleeding", "score": 25},
            {"label": "Hold and wait it out", "score": 65},
            {"label": "Hold, and buy more if I have cash", "score": 100},
        ],
    },
    {
        "id": "past_experience",
        "dimension": "tolerance",
        "weight": 1.5,
        "question": "Have you lived through a market crash while invested?",
        "help": "Reading about 2008 or March 2020 is different from having money in it.",
        "options": [
            {"label": "No, I'm new to investing", "score": 30},
            {"label": "Yes, and I sold", "score": 20},
            {"label": "Yes, and I held on", "score": 80},
            {"label": "Yes, and I bought more", "score": 100},
        ],
    },
    {
        "id": "knowledge",
        "dimension": "tolerance",
        "weight": 1.0,
        "question": "How well do you understand the instruments you invest in?",
        "help": "Not a test — understanding what you own is what stops you selling at the wrong moment.",
        "options": [
            {"label": "Beginner — mostly following advice", "score": 25},
            {"label": "I understand stocks and mutual funds", "score": 55},
            {"label": "Comfortable with ETFs, REITs, bonds and yields", "score": 85},
            {"label": "I analyse financials and value instruments myself", "score": 100},
        ],
    },
    {
        "id": "goal",
        "dimension": "tolerance",
        "weight": 1.5,
        "question": "What is this portfolio actually for?",
        "options": [
            {"label": "Protecting capital — I can't afford to lose it", "score": 10},
            {"label": "Steady income", "score": 35},
            {"label": "Beating inflation with moderate growth", "score": 65},
            {"label": "Maximum long-term growth, volatility accepted", "score": 100},
        ],
    },
    {
        "id": "volatility_comfort",
        "dimension": "tolerance",
        "weight": 1.5,
        "question": "Which one-year outcome would you rather sign up for?",
        "help": "Both have the same expected return. The difference is the ride.",
        "options": [
            {"label": "+6% guaranteed, no possibility of loss", "score": 10},
            {"label": "Between −5% and +18%", "score": 40},
            {"label": "Between −20% and +35%", "score": 75},
            {"label": "Between −40% and +60%", "score": 100},
        ],
    },
]


PROFILES: dict[str, dict[str, Any]] = {
    "conservative": {
        "label": "Conservative",
        "range": (0, 30),
        "summary": "Capital preservation first. Accepts lower returns to avoid meaningful drawdowns.",
        "max_drawdown_tolerance": "5-10%",
        "allocation": {"equity": 15, "etf": 5, "reit": 5, "invit": 5, "bond": 25, "gsec": 30, "gold": 10, "cash": 5},
        "suitable_classes": ["gsec", "bond", "cash", "gold", "invit"],
        "caution_classes": ["equity", "etf", "reit"],
        "avoid_classes": ["commodity"],
    },
    "moderately_conservative": {
        "label": "Moderately Conservative",
        "range": (30, 45),
        "summary": "Income-led with a growth sleeve. Some volatility accepted for a real return above inflation.",
        "max_drawdown_tolerance": "10-15%",
        "allocation": {"equity": 25, "etf": 10, "reit": 10, "invit": 7, "bond": 20, "gsec": 18, "gold": 7, "cash": 3},
        "suitable_classes": ["gsec", "bond", "reit", "invit", "gold", "etf"],
        "caution_classes": ["equity", "commodity"],
        "avoid_classes": [],
    },
    "balanced": {
        "label": "Balanced",
        "range": (45, 62),
        "summary": "Growth and stability weighted roughly evenly. The default for most long-term retail investors.",
        "max_drawdown_tolerance": "15-25%",
        "allocation": {"equity": 40, "etf": 15, "reit": 8, "invit": 5, "bond": 15, "gsec": 8, "gold": 7, "cash": 2},
        "suitable_classes": ["equity", "etf", "reit", "invit", "bond", "gsec", "gold"],
        "caution_classes": ["commodity"],
        "avoid_classes": [],
    },
    "growth": {
        "label": "Growth",
        "range": (62, 80),
        "summary": "Equity-led. Expects and accepts significant drawdowns in exchange for long-run compounding.",
        "max_drawdown_tolerance": "25-35%",
        "allocation": {"equity": 55, "etf": 18, "reit": 7, "invit": 4, "bond": 7, "gsec": 3, "gold": 5, "cash": 1},
        "suitable_classes": ["equity", "etf", "reit", "invit", "gold", "commodity"],
        "caution_classes": [],
        "avoid_classes": [],
    },
    "aggressive": {
        "label": "Aggressive",
        "range": (80, 101),
        "summary": "Maximum growth orientation. Concentrated equity risk with minimal defensive ballast.",
        "max_drawdown_tolerance": "35%+",
        "allocation": {"equity": 70, "etf": 15, "reit": 5, "invit": 2, "bond": 3, "gsec": 0, "gold": 4, "cash": 1},
        "suitable_classes": ["equity", "etf", "commodity", "reit", "invit", "gold"],
        "caution_classes": [],
        "avoid_classes": [],
    },
}


def get_questions() -> list[dict[str, Any]]:
    """The questionnaire, with scores stripped so answers can't be gamed."""
    return [
        {
            "id": q["id"],
            "dimension": q["dimension"],
            "question": q["question"],
            "help": q.get("help"),
            "options": [{"label": o["label"], "index": i} for i, o in enumerate(q["options"])],
        }
        for q in QUESTIONS
    ]


def _profile_for(score: float) -> str:
    for name, spec in PROFILES.items():
        low, high = spec["range"]
        if low <= score < high:
            return name
    return "balanced"


def score_answers(answers: dict[str, int]) -> dict[str, Any]:
    """
    Turn questionnaire answers into a profile.

    `answers` maps question id -> chosen option index.
    """
    missing = [q["id"] for q in QUESTIONS if q["id"] not in answers]
    if missing:
        raise ValueError(f"Unanswered questions: {', '.join(missing)}")

    dimensions: dict[str, dict[str, float]] = {
        "capacity": {"points": 0.0, "weight": 0.0},
        "tolerance": {"points": 0.0, "weight": 0.0},
    }
    horizon_years = 5.0
    detail: list[dict[str, Any]] = []

    for question in QUESTIONS:
        index = answers[question["id"]]
        if not isinstance(index, int) or not 0 <= index < len(question["options"]):
            raise ValueError(f"Invalid answer for '{question['id']}': {index}")

        option = question["options"][index]
        bucket = dimensions[question["dimension"]]
        bucket["points"] += option["score"] * question["weight"]
        bucket["weight"] += question["weight"]

        if question["id"] == "horizon":
            horizon_years = float(option.get("meta", {}).get("years", 5))

        detail.append(
            {
                "id": question["id"],
                "dimension": question["dimension"],
                "answer": option["label"],
                "score": option["score"],
            }
        )

    capacity = dimensions["capacity"]["points"] / dimensions["capacity"]["weight"]
    tolerance = dimensions["tolerance"]["points"] / dimensions["tolerance"]["weight"]

    # The binding constraint is whichever is lower — but not brutally so, or a
    # single cautious answer would override everything else. A 70/30 blend
    # toward the lower value keeps it conservative without being absolute.
    lower, higher = min(capacity, tolerance), max(capacity, tolerance)
    combined = lower * 0.7 + higher * 0.3

    profile_name = _profile_for(combined)
    spec = PROFILES[profile_name]
    gap = abs(capacity - tolerance)

    return {
        "profile": profile_name,
        "label": spec["label"],
        "summary": spec["summary"],
        "capacity_score": round(capacity, 1),
        "tolerance_score": round(tolerance, 1),
        "combined_score": round(combined, 1),
        "binding_constraint": "capacity" if capacity < tolerance else "tolerance",
        "mismatch": _mismatch_note(capacity, tolerance, gap),
        "horizon_years": horizon_years,
        "max_drawdown_tolerance": spec["max_drawdown_tolerance"],
        "target_allocation": spec["allocation"],
        "suitable_classes": spec["suitable_classes"],
        "caution_classes": spec["caution_classes"],
        "avoid_classes": spec["avoid_classes"],
        "answers": detail,
        "assessed_at": datetime.now().isoformat(),
        "disclaimer": (
            "This is a suitability assessment, not investment advice. It does not "
            "account for tax position, insurance cover or existing liabilities."
        ),
    }


def _mismatch_note(capacity: float, tolerance: float, gap: float) -> str | None:
    """Flag the interesting case: circumstances and temperament disagreeing."""
    if gap < 20:
        return None
    if capacity > tolerance:
        return (
            f"Your circumstances could support more risk (capacity {capacity:.0f}) than "
            f"you're comfortable with (tolerance {tolerance:.0f}). That's fine — but the "
            f"gap is worth closing with knowledge rather than by forcing yourself into "
            f"positions you'd sell in a panic."
        )
    return (
        f"You're comfortable with more risk (tolerance {tolerance:.0f}) than your "
        f"circumstances currently support (capacity {capacity:.0f}). Build the emergency "
        f"buffer and lengthen the horizon before taking the risk you're willing to take."
    )


# --- Persistence ------------------------------------------------------------


def save_profile(result: dict[str, Any], user_id: str = "default") -> int:
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO risk_profiles
                (user_id, profile, capacity_score, tolerance_score, combined_score,
                 horizon_years, answers, target_allocation)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                result["profile"],
                result["capacity_score"],
                result["tolerance_score"],
                result["combined_score"],
                result["horizon_years"],
                json.dumps(result["answers"]),
                json.dumps(result["target_allocation"]),
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0
    finally:
        conn.close()


def current_profile(user_id: str = "default") -> dict[str, Any] | None:
    """Most recent assessment, rehydrated with its profile spec."""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM risk_profiles WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    except Exception as exc:
        log.warning("could not read risk profile: %s", exc)
        return None
    finally:
        conn.close()

    if not row:
        return None

    spec = PROFILES.get(row["profile"], PROFILES["balanced"])
    return {
        "profile": row["profile"],
        "label": spec["label"],
        "summary": spec["summary"],
        "capacity_score": row["capacity_score"],
        "tolerance_score": row["tolerance_score"],
        "combined_score": row["combined_score"],
        "horizon_years": row["horizon_years"],
        "max_drawdown_tolerance": spec["max_drawdown_tolerance"],
        "target_allocation": json.loads(row["target_allocation"] or "{}"),
        "suitable_classes": spec["suitable_classes"],
        "caution_classes": spec["caution_classes"],
        "avoid_classes": spec["avoid_classes"],
        "answers": json.loads(row["answers"] or "[]"),
        "assessed_at": row["created_at"],
    }


def profile_history(user_id: str = "default", limit: int = 10) -> list[dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, profile, combined_score, created_at FROM risk_profiles "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]

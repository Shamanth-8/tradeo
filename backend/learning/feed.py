"""
The learning feed — what is new in the market, and why it matters to you.

The problem this solves is the second half of the original brief: instruments
keep arriving and rules keep changing, and investor understanding lags. A
retail investor finds out that a new REIT listed, or that the taxation of a
debt fund changed, or that a stock they hold has moved to a surveillance
measure, roughly never.

Four sources, chosen so the feed works before any key is added:

  * SEBI circulars (no key)      — new instrument categories, regulation changes.
                                   This is where a REIT/InvIT rule change appears
                                   first, months before anyone writes it up.
  * NSE circulars (no key)       — new listings, surveillance measures, trading
                                   halts. The surveillance ones matter most:
                                   they are an early warning on something held.
  * RBI press releases (no key)  — rates, G-Sec auctions, liquidity. The bond
                                   and G-Sec sleeve is unreadable without these.
  * Finnhub (free key)           — IPO and earnings calendars. The only source
                                   here covering things that have not happened
                                   yet, which is what "coming up" means.

The feed is deliberately not summarised by a model on ingest. Items are
classified by rule, ranked by relevance to what the user actually holds, and
only *explained* by the model on demand. Summarising 80 circulars a day with a
3B model on CPU would cost half an hour and add nothing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import requests

from core.config import get_settings
from data.cache import cached

log = logging.getLogger("tradeo.learning")

FINNHUB_BASE = "https://finnhub.io/api/v1"

RSS_SOURCES: dict[str, dict[str, str]] = {
    "sebi": {
        "url": "https://www.sebi.gov.in/sebirss.xml",
        "label": "SEBI",
        "why": "Regulation, new instrument categories, enforcement",
    },
    "nse": {
        "url": "https://nsearchives.nseindia.com/content/RSS/Circulars.xml",
        "label": "NSE",
        "why": "Listings, surveillance measures, trading changes",
    },
    "rbi": {
        "url": "https://www.rbi.org.in/pressreleases_rss.xml",
        "label": "RBI",
        "why": "Rates, G-Sec auctions, liquidity",
    },
}

# Rule-based classification. The first matching category wins, so ordering is
# load-bearing.
#
# Enforcement deliberately comes first. SEBI titles like "Order in the matter
# of IPO irregularities" contain listing vocabulary but are the opposite of a
# new listing, and letting `new_instrument` match first put enforcement orders
# at the top of the learning feed — exactly the noise the feed exists to filter.
# Enforcement language ("adjudication", "order in the matter") is unambiguous,
# so matching it first is safe.
CATEGORY_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("enforcement", "Enforcement",
     ("adjudication", "order in the matter", "in respect to", "penalty", "debarred",
      "prohibited", "settlement order", "show cause", "recovery certificate",
      "irregularities", "unauthorised", "front running")),
    ("new_instrument", "New instrument",
     ("new listing", "listing of", "commencement of trading", "public issue",
      "further public offer", "fpo", "rights issue", "new fund offer", "nfo",
      "debut", "admitted to dealings", "ipo of", "initial public offer")),
    ("surveillance", "Surveillance / risk",
     ("surveillance measure", "asm", "gsm", "graded surveillance", "additional surveillance",
      "trade for trade", "circuit", "suspension", "suspended", "ibc", "insolvency")),
    ("reit_invit", "REIT / InvIT",
     ("reit", "invit", "infrastructure investment trust", "real estate investment trust")),
    ("bonds", "Bonds / G-Sec",
     ("g-sec", "gsec", "government securities", "treasury bill", "t-bill", "auction",
      "state development loan", "sdl", "debenture", "ncd", "bond", "sovereign gold bond",
      "repo rate", "yield")),
    ("etf_mf", "ETF / Mutual fund",
     ("etf", "exchange traded fund", "mutual fund", "index fund", "amc")),
    ("regulation", "Rule change",
     ("circular", "amendment", "regulations", "framework", "guidelines", "master direction",
      "consultation paper", "disclosure requirement")),
    ("corporate_action", "Corporate action",
     ("bonus", "split", "dividend", "buyback", "merger", "demerger", "scheme of arrangement",
      "capital reduction", "delisting")),
]

# Categories worth surfacing to someone trying to learn, versus noise. SEBI's
# feed in particular is dominated by enforcement orders against individuals,
# which teach nothing.
LEARNING_VALUE = {
    "new_instrument": 5,
    "reit_invit": 5,
    "bonds": 4,
    "regulation": 4,
    "etf_mf": 3,
    "surveillance": 3,
    "corporate_action": 2,
    "enforcement": 1,
    "general": 1,
}


@dataclass
class FeedItem:
    """One thing that happened, normalised across very different sources."""

    title: str
    source: str
    source_label: str
    url: str = ""
    published: str = ""
    summary: str = ""
    category: str = "general"
    category_label: str = "General"
    symbols: list[str] = field(default_factory=list)
    relevance: int = 0
    learning_value: int = 1

    @property
    def score(self) -> int:
        """Ranking: what you hold beats what is merely educational."""
        return self.relevance * 10 + self.learning_value

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "source_label": self.source_label,
            "url": self.url,
            "published": self.published,
            "summary": self.summary,
            "category": self.category,
            "category_label": self.category_label,
            "symbols": self.symbols,
            "relevance": self.relevance,
            "learning_value": self.learning_value,
            "score": self.score,
        }


def classify(text: str) -> tuple[str, str]:
    lowered = text.lower()
    for key, label, patterns in CATEGORY_RULES:
        if any(pattern in lowered for pattern in patterns):
            return key, label
    return "general", "General"


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").replace("&nbsp;", " ").strip()


def _match_symbols(text: str, universe: Iterable[str]) -> list[str]:
    """
    Which held instruments does this item mention?

    Word-boundary matching, and symbols shorter than three characters are
    skipped entirely — "M&M" or "IEX" inside ordinary prose produces constant
    false positives that make the relevance ranking useless.
    """
    upper = text.upper()
    hits: list[str] = []
    for symbol in universe:
        if len(symbol) < 3:
            continue
        if re.search(rf"\b{re.escape(symbol)}\b", upper):
            hits.append(symbol)
    return hits[:5]


# ---- sources ---------------------------------------------------------------


@cached(ttl=900, prefix="learning.rss", skip_if=lambda r: not r)
def _fetch_rss(source_key: str) -> list[dict[str, Any]]:
    """One RSS source, parsed. Cached because these change a few times a day."""
    import feedparser

    config = RSS_SOURCES.get(source_key)
    if not config:
        return []

    try:
        # feedparser can fetch, but NSE rejects the default user agent, so the
        # bytes are fetched here and handed over already downloaded.
        response = requests.get(
            config["url"],
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Tradeo/1.0)"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("learning feed %s unavailable: %s", source_key, exc)
        return []

    parsed = feedparser.parse(response.content)
    items: list[dict[str, Any]] = []
    for entry in parsed.entries[:40]:
        title = _strip_html(entry.get("title", ""))
        if not title or title.lower().startswith(("sebi rss", "press releases from rbi",
                                                  "nse circulars", "circular homepage")):
            continue
        items.append({
            "title": title,
            "url": entry.get("link", ""),
            "published": entry.get("published", "") or entry.get("updated", ""),
            "summary": _strip_html(entry.get("summary", ""))[:400],
        })
    return items


def _finnhub(path: str, params: dict[str, Any] | None = None) -> Any:
    settings = get_settings()
    if not settings.finnhub_api_key:
        return None
    try:
        response = requests.get(
            f"{FINNHUB_BASE}/{path}",
            params={**(params or {}), "token": settings.finnhub_api_key},
            timeout=20,
        )
    except requests.RequestException as exc:
        log.warning("finnhub %s failed: %s", path, exc)
        return None

    if response.status_code == 403 or response.status_code == 401:
        log.info("finnhub %s not available on this plan", path)
        return None
    if response.status_code >= 400:
        log.warning("finnhub %s returned HTTP %s", path, response.status_code)
        return None
    try:
        return response.json()
    except ValueError:
        return None


@cached(ttl=3600, prefix="learning.ipo", skip_if=lambda r: not r)
def ipo_calendar(days_ahead: int = 45) -> list[dict[str, Any]]:
    """
    Upcoming IPOs.

    Finnhub's free tier covers US listings, not Indian ones. That is worth
    being honest about rather than pretending otherwise: it is still the best
    free view of *what an IPO calendar looks like* and of global listings, and
    Indian IPOs arrive through the SEBI and NSE circular feeds instead.
    """
    today = date.today()
    payload = _finnhub("calendar/ipo", {
        "from": today.isoformat(),
        "to": (today + timedelta(days=days_ahead)).isoformat(),
    })
    if not isinstance(payload, dict):
        return []

    rows = []
    for row in payload.get("ipoCalendar") or []:
        rows.append({
            "date": row.get("date"),
            "name": row.get("name"),
            "symbol": row.get("symbol"),
            "exchange": row.get("exchange"),
            "price": row.get("price"),
            "shares": row.get("numberOfShares"),
            "value": row.get("totalSharesValue"),
            "status": row.get("status"),
        })
    rows.sort(key=lambda r: r.get("date") or "")
    return rows


@cached(ttl=3600, prefix="learning.earnings", skip_if=lambda r: not r)
def earnings_calendar(days_ahead: int = 14) -> list[dict[str, Any]]:
    """Upcoming earnings, so a result is never a surprise."""
    today = date.today()
    payload = _finnhub("calendar/earnings", {
        "from": today.isoformat(),
        "to": (today + timedelta(days=days_ahead)).isoformat(),
    })
    if not isinstance(payload, dict):
        return []

    rows = []
    for row in payload.get("earningsCalendar") or []:
        rows.append({
            "date": row.get("date"),
            "symbol": row.get("symbol"),
            "quarter": row.get("quarter"),
            "year": row.get("year"),
            "eps_estimate": row.get("epsEstimate"),
            "revenue_estimate": row.get("revenueEstimate"),
        })
    rows.sort(key=lambda r: r.get("date") or "")
    return rows


@cached(ttl=900, prefix="learning.news", skip_if=lambda r: not r)
def market_news(limit: int = 25) -> list[dict[str, Any]]:
    """General market news from Finnhub."""
    payload = _finnhub("news", {"category": "general"})
    if not isinstance(payload, list):
        return []
    return [
        {
            "title": row.get("headline"),
            "url": row.get("url"),
            "source": row.get("source"),
            "summary": (row.get("summary") or "")[:400],
            "published": datetime.fromtimestamp(row["datetime"]).isoformat()
            if row.get("datetime") else "",
        }
        for row in payload[:limit]
    ]


# ---- the feed itself -------------------------------------------------------


def _held_symbols() -> list[str]:
    try:
        from brokers.registry import registry

        return [row["symbol"] for row in registry.consolidated_holdings()["holdings"]]
    except Exception:
        return []


def build_feed(limit: int = 40, category: str | None = None,
               min_learning_value: int = 0) -> dict[str, Any]:
    """
    Everything new, ranked.

    Ranking puts anything touching a holding at the top, then sorts by how much
    there is to learn from it. That ordering is the whole point: a surveillance
    measure on something you own is not the same class of event as an
    adjudication order against a stranger.
    """
    held = _held_symbols()
    items: list[FeedItem] = []

    for key, config in RSS_SOURCES.items():
        for row in _fetch_rss(key):
            blob = f"{row['title']} {row['summary']}"
            cat_key, cat_label = classify(blob)
            symbols = _match_symbols(blob, held)
            items.append(FeedItem(
                title=row["title"],
                source=key,
                source_label=config["label"],
                url=row["url"],
                published=row["published"],
                summary=row["summary"],
                category=cat_key,
                category_label=cat_label,
                symbols=symbols,
                relevance=len(symbols),
                learning_value=LEARNING_VALUE.get(cat_key, 1),
            ))

    for row in market_news(limit=20):
        cat_key, cat_label = classify(f"{row['title']} {row['summary']}")
        items.append(FeedItem(
            title=row["title"] or "",
            source="finnhub",
            source_label=row.get("source") or "Finnhub",
            url=row.get("url") or "",
            published=row.get("published") or "",
            summary=row.get("summary") or "",
            category=cat_key,
            category_label=cat_label,
            learning_value=LEARNING_VALUE.get(cat_key, 1),
        ))

    if category:
        items = [i for i in items if i.category == category]
    if min_learning_value:
        items = [i for i in items if i.learning_value >= min_learning_value]

    items.sort(key=lambda i: i.score, reverse=True)

    counts: dict[str, int] = {}
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1

    return {
        "items": [i.as_dict() for i in items[:limit]],
        "total": len(items),
        "by_category": counts,
        "affecting_holdings": [i.as_dict() for i in items if i.relevance][:10],
        "sources": {
            key: {"label": cfg["label"], "why": cfg["why"]}
            for key, cfg in RSS_SOURCES.items()
        },
        "finnhub_configured": bool(get_settings().finnhub_api_key),
    }


def whats_new(limit: int = 12) -> dict[str, Any]:
    """
    The learning view: only things that teach you something.

    Filters out enforcement noise and single-company corporate actions, which
    dominate by volume and teach nothing about how the market works.
    """
    feed = build_feed(limit=limit * 3, min_learning_value=3)
    return {
        "items": feed["items"][:limit],
        "upcoming_ipos": ipo_calendar()[:8],
        "upcoming_earnings": earnings_calendar()[:10],
        "affecting_your_holdings": feed["affecting_holdings"],
        "by_category": feed["by_category"],
    }


def explain(item_title: str, item_summary: str = "") -> dict[str, Any]:
    """
    Ask the local model what one item actually means for this user.

    On demand only. This is the expensive operation in the module and it exists
    to answer "so what?", which is exactly the question a circular never
    answers.
    """
    from ai.brain import brain

    held = _held_symbols()
    prompt = f"""A retail investor in India is trying to understand this market announcement.

ANNOUNCEMENT
  {item_title}
  {item_summary[:600]}

THEY CURRENTLY HOLD: {', '.join(held[:15]) if held else 'nothing recorded yet'}

Explain it the way a good broker would explain it to a client over the phone:
what it is, why it exists, whether it affects them, and what — if anything — \
they should actually do. If it does not affect them, say so plainly rather than \
manufacturing relevance. Do not give tax advice. Four short paragraphs maximum."""

    try:
        answer = brain.think(
            prompt,
            task="education",
            register="screen",
            prefer="local",
            max_tokens=600,
        )
    except Exception as exc:
        return {"explanation": None, "error": str(exc)}

    return {
        "title": item_title,
        "explanation": answer.text,
        "model": answer.model,
        "provider": answer.provider,
        "holdings_considered": len(held),
    }


def status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "enabled": settings.learning_enabled,
        "finnhub_configured": bool(settings.finnhub_api_key),
        "rss_sources": list(RSS_SOURCES),
        "keyless_sources_work_without_any_api": True,
    }

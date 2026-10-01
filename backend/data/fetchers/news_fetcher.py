"""
Financial news from Indian market RSS feeds.

The feeds themselves are fetched once and cached; keyword filtering happens on
the cached copy. Before that split, scanning 87 symbols meant 87 × 4 RSS
round trips for what is the same handful of articles.
"""

import ssl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List

import re

import feedparser
import requests

from data.cache import cached

# Handle SSL context for some RSS feeds
if hasattr(ssl, "_create_unverified_context"):
    ssl._create_default_https_context = ssl._create_unverified_context

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
TTL_FEEDS = 600  # 10 minutes — news does not break faster than that


class NewsFetcher:
    """Fetch financial news from RSS feeds."""

    RSS_FEEDS = {
        "MoneyControl": "https://www.moneycontrol.com/rss/latestnews.xml",
        "Economic Times": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
        "Live Mint": "https://www.livemint.com/rss/markets",
        "Business Standard": "https://www.business-standard.com/rss/finance-news-103.rss",
    }

    def _fetch_one(self, source_url: tuple) -> List[Dict[str, Any]]:
        source, url = source_url
        items: List[Dict[str, Any]] = []
        try:
            # Fetched with a browser user agent: Business Standard answers
            # feedparser's default one with 403, which parsed as an empty feed.
            response = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=15)
            feed = feedparser.parse(response.content)
            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                if not title:
                    continue
                try:
                    pub_date = datetime(*entry.published_parsed[:6])
                except (AttributeError, TypeError, ValueError):
                    pub_date = datetime.now()

                items.append(
                    {
                        "title": title,
                        "summary": entry.get("summary", ""),
                        "source": source,
                        "url": entry.get("link", ""),
                        "published_at": pub_date,
                    }
                )
        except Exception as e:
            print(f"Error fetching from {source}: {e}")
        return items

    @cached(ttl=TTL_FEEDS, prefix="rss_all", persist=True, skip_if=lambda r: not r)
    def fetch_all(self) -> List[Dict[str, Any]]:
        """Every article across every feed, newest first. Feeds run in parallel."""
        with ThreadPoolExecutor(max_workers=len(self.RSS_FEEDS)) as pool:
            batches = pool.map(self._fetch_one, self.RSS_FEEDS.items())

        articles = [item for batch in batches for item in batch]
        articles.sort(key=lambda x: x["published_at"], reverse=True)
        return articles

    def fetch_latest_news(self, keywords: List[str] = []) -> List[Dict[str, Any]]:
        """
        News, optionally filtered to articles mentioning any of `keywords`.

        Args:
            keywords: Stock symbols or company names. Empty means no filter.
        """
        articles = self.fetch_all()

        if not keywords:
            return [{**a, "related_symbols": "MARKET"} for a in articles]

        # Whole words only: a bare substring test counted "resuLTs", "STL" and
        # "muLTibagger" as news about LT (Larsen & Toubro).
        patterns = {k.upper(): re.compile(rf"(?<![A-Z0-9]){re.escape(k.upper())}(?![A-Z0-9])")
                    for k in keywords if k}
        matched: List[Dict[str, Any]] = []
        for article in articles:
            haystack = f"{article['title']} {article['summary']}".upper()
            hits = [n for n, pattern in patterns.items() if pattern.search(haystack)]
            if hits:
                matched.append({**article, "related_symbols": ",".join(hits)})
        return matched


news_fetcher = NewsFetcher()

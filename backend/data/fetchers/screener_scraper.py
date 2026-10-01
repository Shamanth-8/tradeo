"""
Screener.in Web Scraper
Fetch deep Indian market fundamentals (10-year data)
"""

import requests
from bs4 import BeautifulSoup
import re
from typing import Dict, Any, List, Optional


class ScreenerScraper:
    """Scrape financial data from Screener.in for Indian stocks."""

    BASE_URL = "https://www.screener.in"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )

    def get_company_data(self, symbol: str) -> Dict[str, Any]:
        """
        Get comprehensive company data from Screener.in.

        Returns 10+ years of: ratios, quarterly results, P&L, balance sheet,
        cash flow, shareholding patterns.
        """
        url = f"{self.BASE_URL}/company/{symbol}/consolidated/"

        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 404:
                # Try standalone instead of consolidated
                url = f"{self.BASE_URL}/company/{symbol}/"
                response = self.session.get(url, timeout=15)

            if response.status_code != 200:
                return {
                    "error": f"Could not fetch data for {symbol}",
                    "status": response.status_code,
                }

            soup = BeautifulSoup(response.text, "html.parser")

            data = {
                "symbol": symbol,
                "ratios": self._extract_ratios(soup),
                "top_ratios": self._extract_top_ratios(soup),
                "quarterly_results": self._extract_table(soup, "quarters"),
                "profit_loss": self._extract_table(soup, "profit-loss"),
                "balance_sheet": self._extract_table(soup, "balance-sheet"),
                "cash_flow": self._extract_table(soup, "cash-flow"),
                "shareholding": self._extract_table(soup, "shareholding"),
                "peers": self._extract_peers(soup),
            }

            return data

        except requests.exceptions.Timeout:
            return {"error": f"Timeout fetching data for {symbol}"}
        except Exception as e:
            return {"error": str(e)}

    def _extract_top_ratios(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract the top-level ratio boxes (Market Cap, PE, ROCE, ROE, etc.)."""
        ratios = {}
        ratio_list = soup.find("ul", {"id": "top-ratios"})
        if not ratio_list:
            ratio_list = soup.find("ul", class_="flex-list")

        if ratio_list:
            items = ratio_list.find_all("li")
            for item in items:
                name_el = item.find("span", class_="name")
                value_el = item.find("span", class_="number")
                if name_el and value_el:
                    name = name_el.text.strip().rstrip(":")
                    value = self._parse_number(value_el.text.strip())
                    ratios[name] = value

        return ratios

    def _extract_ratios(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract key ratios from the ratios section."""
        ratios = {}
        section = soup.find("section", {"id": "ratios"})
        if not section:
            return ratios

        table = section.find("table")
        if not table:
            return ratios

        rows = table.find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 2:
                key = cols[0].text.strip()
                # Get the most recent value (last column)
                value = cols[-1].text.strip()
                ratios[key] = self._parse_number(value)

        return ratios

    def _extract_table(self, soup: BeautifulSoup, section_id: str) -> List[Dict]:
        """Extract data from a screener table section."""
        section = soup.find("section", {"id": section_id})
        if not section:
            return []

        table = section.find("table")
        if not table:
            return []

        # Get headers
        headers = []
        thead = table.find("thead")
        if thead:
            for th in thead.find_all("th"):
                headers.append(th.text.strip())

        # Get rows
        rows_data = []
        tbody = table.find("tbody")
        if tbody:
            for row in tbody.find_all("tr"):
                cells = row.find_all("td")
                if cells and headers:
                    row_dict = {}
                    for i, cell in enumerate(cells):
                        if i < len(headers):
                            row_dict[headers[i]] = self._parse_number(cell.text.strip())
                    rows_data.append(row_dict)

        return rows_data

    def _extract_peers(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract peer comparison data."""
        peers = []
        section = soup.find("section", {"id": "peers"})
        if not section:
            return peers

        table = section.find("table")
        if not table:
            return peers

        headers = []
        thead = table.find("thead")
        if thead:
            for th in thead.find_all("th"):
                headers.append(th.text.strip())

        tbody = table.find("tbody")
        if tbody:
            for row in tbody.find_all("tr")[:10]:  # Top 10 peers
                cells = row.find_all("td")
                if cells and headers:
                    peer = {}
                    for i, cell in enumerate(cells):
                        if i < len(headers):
                            # Check if it's a link (company name)
                            link = cell.find("a")
                            if link:
                                peer[headers[i]] = link.text.strip()
                            else:
                                peer[headers[i]] = self._parse_number(cell.text.strip())
                    peers.append(peer)

        return peers

    def _parse_number(self, text: str) -> Any:
        """Parse a number string, handling commas, percentages, and CR/Lakh notation."""
        if not text or text == "--" or text == "":
            return None

        # Remove commas
        text = text.replace(",", "").strip()

        # Handle percentage
        if text.endswith("%"):
            try:
                return float(text.rstrip("%"))
            except ValueError:
                return text

        # Handle Cr (Crore)
        if "Cr" in text:
            try:
                return float(text.replace("Cr", "").strip())
            except ValueError:
                return text

        # Try to parse as number
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text

    def get_extended_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """
        Get extended fundamental metrics not available in yfinance.

        Returns: ROIC, ROCE, interest coverage, promoter holding/pledging,
        5-year growth CAGRs, book value growth, dividend growth.
        """
        data = self.get_company_data(symbol)
        if "error" in data:
            return data

        top_ratios = data.get("top_ratios", {})
        ratios = data.get("ratios", {})

        extended = {
            "symbol": symbol,
            "roce": top_ratios.get("ROCE", ratios.get("ROCE %")),
            "roic": ratios.get("ROIC %"),
            "interest_coverage": ratios.get("Interest Coverage"),
            "book_value_per_share": top_ratios.get("Book Value"),
            "promoter_holding": ratios.get("Promoter Holding"),
            "promoter_pledging": ratios.get("Pledged %"),
            "revenue_5yr_cagr": ratios.get("Sales Growth (5Yr)"),
            "earnings_5yr_cagr": ratios.get("Profit Growth (5Yr)"),
            "dividend_growth_5yr": ratios.get("Div Yield"),
            "peers": data.get("peers", []),
            "quarterly_results": data.get("quarterly_results", []),
        }

        return extended


# Singleton instance
screener_scraper = ScreenerScraper()

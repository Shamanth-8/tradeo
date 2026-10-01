"""
Broker registry — the fusion point.

Holds every configured account and merges them into one consolidated view.
Merging is the interesting part: the same stock held at two brokers is one
economic exposure, so quantities combine and the average price is
cost-weighted, while the per-broker breakdown is preserved so you can still
see where it sits.

Sources are queried in parallel and a failing broker degrades to a warning
rather than emptying the dashboard.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .base import BrokerAdapter, Funds, Holding, Position

log = logging.getLogger("tradeo.brokers")

PLUGIN_DIR = Path(__file__).parent / "plugins"


def builtin_adapters() -> list[type[BrokerAdapter]]:
    """Adapters that ship with Tradeo. Imported lazily so one broken import can't sink the rest."""
    specs = [
        ("manual", "ManualBroker"),          # holdings you type in
        ("depository", "DepositoryBroker"),  # CDSL/NSDL statement import
        ("angelone", "AngelOneBroker"),
        ("dhan", "DhanBroker"),
        ("zerodha", "ZerodhaBroker"),
        ("kotakneo", "KotakNeoBroker"),
    ]
    classes = []
    for module, cls in specs:
        try:
            classes.append(getattr(importlib.import_module(f".{module}", __package__), cls))
        except Exception as exc:
            log.warning("broker %s unavailable: %s", module, exc)
    return classes


def plugin_adapters() -> list[type[BrokerAdapter]]:
    """
    Every BrokerAdapter subclass in brokers/plugins/*.py (files starting with
    "_" are skipped). This is how you add a broker Tradeo doesn't ship: see
    brokers/plugins/README.md.
    """
    classes = []
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"tradeo_broker_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            log.warning("broker plugin %s failed to load: %s", path.name, exc)
            continue
        for obj in vars(module).values():
            if (inspect.isclass(obj) and issubclass(obj, BrokerAdapter) and obj is not BrokerAdapter
                    and not inspect.isabstract(obj) and obj.__module__ == module.__name__):
                classes.append(obj)
                log.info("broker plugin loaded: %s (%s)", obj.display_name, path.name)
    return classes


class BrokerRegistry:
    """Every connected source of holdings, fused."""

    def __init__(self) -> None:
        self._adapters: dict[str, BrokerAdapter] = {}
        self.reload()

    def reload(self) -> None:
        """(Re)build every adapter, so new credentials take effect without a restart."""
        try:
            from .dhan import auth as dhan_auth

            dhan_auth.invalidate()  # a cached token must not outlive a new PIN/TOTP
        except Exception:
            pass

        adapters: dict[str, BrokerAdapter] = {}
        for cls in builtin_adapters() + plugin_adapters():
            try:
                adapter = cls()
            except Exception as exc:
                log.warning("could not start broker %s: %s", getattr(cls, "name", cls), exc)
                continue
            adapters[adapter.name] = adapter
            if cls.credential_fields:
                try:
                    from core import credentials

                    credentials.register_fields(cls.credential_fields)
                except Exception as exc:
                    log.warning("could not register %s credentials: %s", adapter.name, exc)
        self._adapters = adapters

    def register(self, adapter: BrokerAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def live_broker(self) -> tuple[BrokerAdapter | None, str]:
        """
        The broker live orders go to (LIVE_BROKER), or why there is none.

        Needs the broker connected AND its own <NAME>_ALLOW_TRADING=true; the
        autopilot's AUTOPILOT_MODE=live is the separate second switch.
        """
        from core.config import get_settings

        name = (get_settings().live_broker or "").lower()
        if not name:
            return None, "LIVE_BROKER is not set"
        adapter = self.get(name)
        if adapter is None:
            return None, f"no broker named '{name}'"
        if not adapter.can_trade:
            return None, f"{adapter.display_name} is read-only"
        if not adapter.is_configured():
            return None, f"{adapter.display_name} is not connected"
        if not adapter.trading_allowed:
            return None, f"{name.upper()}_ALLOW_TRADING is off"
        return adapter, "ok"

    def get(self, name: str) -> BrokerAdapter | None:
        return self._adapters.get(name)

    @property
    def all(self) -> list[BrokerAdapter]:
        return list(self._adapters.values())

    @property
    def active(self) -> list[BrokerAdapter]:
        """Adapters with credentials or data — the ones worth querying."""
        return [a for a in self._adapters.values() if a.is_configured()]

    # ---- aggregation ------------------------------------------------------

    def _collect(self, method: str) -> tuple[list[Any], list[str]]:
        """Run `method` on every active adapter in parallel."""
        results: list[Any] = []
        errors: list[str] = []

        adapters = self.active
        if not adapters:
            return results, errors

        with ThreadPoolExecutor(max_workers=max(1, len(adapters))) as pool:
            futures = {pool.submit(getattr(a, method)): a for a in adapters}
            for future in as_completed(futures):
                adapter = futures[future]
                try:
                    results.extend(future.result() or [])
                except Exception as exc:
                    log.warning("%s.%s failed: %s", adapter.name, method, exc)
                    errors.append(f"{adapter.display_name}: {exc}")

        return results, errors

    def all_holdings(self) -> tuple[list[Holding], list[str]]:
        return self._collect("holdings")

    def all_positions(self) -> tuple[list[Position], list[str]]:
        return self._collect("positions")

    def consolidated_holdings(self) -> dict[str, Any]:
        """
        One row per instrument, regardless of how many accounts hold it.

        This is the view that doesn't exist anywhere else for a retail investor
        with more than one demat account.
        """
        holdings, errors = self.all_holdings()

        merged: dict[str, dict[str, Any]] = {}
        for holding in holdings:
            if holding.quantity <= 0:
                continue

            row = merged.setdefault(
                holding.symbol,
                {
                    "symbol": holding.symbol,
                    "name": holding.name or holding.symbol,
                    "quantity": 0.0,
                    "invested": 0.0,
                    "ltp": 0.0,
                    "asset_class": holding.asset_class,
                    "isin": holding.isin,
                    "sources": [],
                },
            )
            row["quantity"] += holding.quantity
            row["invested"] += holding.invested
            # Any non-zero quote wins; adapters differ in whether they carry one.
            row["ltp"] = holding.ltp or row["ltp"]
            row["isin"] = row["isin"] or holding.isin
            row["sources"].append(
                {
                    "broker": holding.broker,
                    "quantity": holding.quantity,
                    "avg_price": round(holding.avg_price, 2),
                }
            )

        rows: list[dict[str, Any]] = []
        for row in merged.values():
            quantity = row["quantity"]
            invested = row["invested"]
            ltp = row["ltp"]
            # Cost-weighted, so two lots at different prices blend correctly.
            avg_price = invested / quantity if quantity else 0.0
            current_value = quantity * (ltp or avg_price)
            pnl = current_value - invested

            rows.append(
                {
                    **row,
                    "quantity": round(quantity, 4),
                    "avg_price": round(avg_price, 2),
                    "ltp": round(ltp, 2),
                    "invested": round(invested, 2),
                    "current_value": round(current_value, 2),
                    "pnl": round(pnl, 2),
                    "pnl_percent": round((pnl / invested * 100) if invested else 0.0, 2),
                    "held_across": len(row["sources"]),
                }
            )

        rows.sort(key=lambda r: r["current_value"], reverse=True)

        invested_total = sum(r["invested"] for r in rows)
        current_total = sum(r["current_value"] for r in rows)

        return {
            "holdings": rows,
            "totals": {
                "invested": round(invested_total, 2),
                "current_value": round(current_total, 2),
                "pnl": round(current_total - invested_total, 2),
                "pnl_percent": round(
                    ((current_total - invested_total) / invested_total * 100)
                    if invested_total
                    else 0.0,
                    2,
                ),
                "instruments": len(rows),
                "accounts": len({s["broker"] for r in rows for s in r["sources"]}),
            },
            "errors": errors,
        }

    def all_funds(self) -> dict[str, Any]:
        per_broker: list[dict[str, Any]] = []
        total = Funds(broker="consolidated")

        for adapter in self.active:
            try:
                funds = adapter.funds()
            except Exception as exc:
                log.warning("%s funds failed: %s", adapter.name, exc)
                continue
            if funds.total or funds.available:
                per_broker.append(funds.as_dict())
                total.available += funds.available
                total.used += funds.used
                total.total += funds.total

        return {"total": total.as_dict(), "by_broker": per_broker}

    def status(self) -> dict[str, Any]:
        """Per-adapter health, for the connections screen."""
        return {
            "brokers": [adapter.status() for adapter in self.all],
            "active": [a.name for a in self.active],
        }


registry = BrokerRegistry()

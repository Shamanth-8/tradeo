# Adding your own broker

Tradeo ships with Zerodha, Dhan, Angel One and Kotak Neo. Any other broker
(Upstox, Groww, ICICI Direct, Fyers, 5paisa…) can be added as a **plugin**:
one Python file in this folder. No other code changes are needed.

Everything is **paper trading by default**. A broker connection adds your real
holdings to the Wealth screen and, if you choose, lets the autopilot place
real orders; neither is required to use Tradeo.

## 1. Copy the template

```bash
cd backend/brokers/plugins
cp _example_broker.py upstox.py        # any name not starting with "_"
```

## 2. Fill in the class

```python
class UpstoxBroker(BrokerAdapter):
    name = "upstox"                     # short id, lowercase
    display_name = "Upstox"
    can_trade = True                    # False = holdings only
    docs_url = "https://upstox.com/developer/api-documentation"
    credential_fields = {               # these appear on the Connections screen
        "UPSTOX_API_KEY":       {"label": "Upstox API key",  "secret": False},
        "UPSTOX_ACCESS_TOKEN":  {"label": "Access token",    "secret": True},
        "UPSTOX_ALLOW_TRADING": {"label": "Allow live orders", "secret": False, "type": "bool"},
    }
```

| Method | Required | Returns |
|---|---|---|
| `is_configured()` | yes | `True` when the credentials are present. **No network calls.** |
| `connect()` | yes | `True` if a session works. Return `False`, don't raise. |
| `holdings()` | yes | `list[Holding]` with `symbol` (NSE symbol, e.g. `INFY`), `quantity`, `avg_price`, `broker=self.name` |
| `positions()` | no | `list[Position]` (intraday/F&O) |
| `funds()` | no | `Funds(available, used, total)` |
| `ltp(symbol)` | no | Live price, or `None` (Yahoo Finance is used instead) |
| `place_order(order)` | only if `can_trade` | `OrderResult`; call `self.require_trading_allowed()` first |

Read credentials with `self.cred("UPSTOX_API_KEY")`. It checks the Connections
screen first, then `backend/.env`. Raise `BrokerAuthError` for an expired or
rejected session and `BrokerError` for anything else; Tradeo shows the message
and carries on with your other accounts.

The data classes (`Holding`, `Position`, `Funds`, `OrderRequest`,
`OrderResult`) are in [`../base.py`](../base.py).

## 3. Add your credentials

Either restart the backend and open **Connections** in the app (your fields
are listed under *Broker*), or put them in `backend/.env`:

```ini
UPSTOX_API_KEY=...
UPSTOX_ACCESS_TOKEN=...
```

Keys entered in the app are saved to `config/credentials.json` (file mode
0600, git-ignored). Never commit either file.

## 4. Check it

```bash
curl -s localhost:8000/api/wealth/brokers | python -m json.tool
```

Your broker should be listed with `"configured": true, "connected": true`,
and its holdings appear on the Wealth screen, merged with any other accounts.

## 5. Live orders (optional, at your own risk)

Orders stay on paper until **all three** are set:

1. `LIVE_BROKER=upstox` (your adapter's `name`),
2. `UPSTOX_ALLOW_TRADING=true`, and
3. the autopilot's live mode, `AUTOPILOT_MODE=live` (see the main README).

Test with the smallest possible quantity first. Tradeo's authors cannot test
every broker; you are responsible for what your plugin sends.

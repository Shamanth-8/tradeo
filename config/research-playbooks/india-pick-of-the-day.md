---
name: India Pick of the Day
description: Report the NSE pick of the day that Tradeo's rule paper-traded at 09:30 IST, and how open picks are doing.
markets: [in]
suggested_schedule: "45 9 * * 1-5"
suggested_timezone: Asia/Kolkata
data_capabilities:
  - Tradeo's pick of the day, chosen by a fixed rule and paper-traded
  - Tradeo's paper account, open paper brackets and closed-trade record
variables: {}
---

# India pick of the day

Tradeo chooses the Indian pick of the day by a fixed rule at 09:30 IST and
paper-trades it. Your job is to report it, not to choose it.

## Steps

1. Call `mcp_tradeo_tradeo_daily_pick`.
2. Call `mcp_tradeo_tradeo_positions`.
3. Report today's pick: the symbol, quantity, entry, stop, target and the
   `reason` field, word for word. If `picked` is false, say that no stock
   passed the rule today.
4. List the open positions with their profit or loss.

## Rules

- Use only `mcp_tradeo_tradeo_daily_pick` and `mcp_tradeo_tradeo_positions`.
  No web search, no URL reading, no code, no other tool.
- Every number comes from those two results. Do not add analysis of your own.

## Verdict

End with one line: `VERDICT: <SYMBOL> picked | no pick`.

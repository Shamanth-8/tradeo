"""
Product education.

The problem statement's second half: new instruments keep arriving and investor
understanding lags, which is exactly the gap mis-selling lives in. So each
asset class gets an honest briefing — what it is, what actually generates the
return, how it's taxed, and specifically how it goes wrong.

The "how it goes wrong" section is the point. Product literature explains the
upside perfectly well; almost nothing explains the failure mode before you own
it. A REIT's distribution is not a fixed deposit's interest, and an InvIT's
yield can be partly a return of your own capital — those are the facts that
change decisions.

Content is static and reviewable rather than model-generated. The LLM is used
only to personalise an explanation against a specific portfolio, where it can't
invent a tax rule.
"""

from __future__ import annotations

from typing import Any

CURRICULUM: dict[str, dict[str, Any]] = {
    "reit": {
        "name": "REITs — Real Estate Investment Trusts",
        "one_liner": "Own a slice of rent-generating commercial property, traded like a share.",
        "level": "intermediate",
        "read_minutes": 6,
        "what_it_is": (
            "A REIT owns and operates income-producing real estate — mostly Grade-A office "
            "parks and malls in India — and is listed on the exchange. You buy units the "
            "same way you buy shares. SEBI requires at least 90% of net distributable cash "
            "flow to be paid out to unitholders, and caps how much can sit in under-"
            "construction assets, so it stays a rental-income vehicle rather than a "
            "property developer."
        ),
        "how_you_earn": [
            "Distributions, usually quarterly, from the rent the properties collect",
            "Capital appreciation if the underlying property value or rental rates rise",
            "Rental escalations built into most Indian commercial leases (typically 12-15% every three years)",
        ],
        "how_it_goes_wrong": [
            "A distribution is not interest. If tenants leave, it falls — there is no guarantee.",
            "Occupancy is the number that matters. Watch it every quarter; a slide from 92% to 84% "
            "hits distributions hard and usually shows up before the unit price reacts.",
            "Interest-rate sensitive on both sides: rate rises increase the REIT's own debt cost "
            "and make its yield less attractive versus a fixed deposit, so units fall.",
            "Concentration you may not notice — Indian REITs are heavily exposed to IT-sector "
            "tenants in a handful of cities. If you also hold IT stocks, that's one bet, not two.",
            "Liquidity is thinner than large-cap equity. Exiting a large position moves the price.",
        ],
        "taxation_india": (
            "Distributions arrive split into components taxed differently: the interest portion "
            "at your slab rate, dividend portion usually taxed if the REIT opted for the "
            "concessional regime, and the 'return of capital' portion not taxed on receipt but "
            "reducing your cost base, which raises capital gains when you sell. Your annual "
            "statement breaks this down — read it before assuming the headline yield is post-tax."
        ),
        "who_its_for": "Investors wanting property exposure and regular income without buying a flat, with a 5+ year horizon.",
        "who_should_avoid": "Anyone needing guaranteed income, or already heavily exposed to real estate or IT-sector tenants.",
        "key_metrics": [
            {"name": "Distribution yield", "why": "Annual payout ÷ price. Compare against the 10-year G-Sec, not against equity returns."},
            {"name": "Occupancy rate", "why": "The leading indicator. Falling occupancy precedes falling distributions."},
            {"name": "WALE", "why": "Weighted Average Lease Expiry. Longer means income is locked in further out."},
            {"name": "Loan-to-value", "why": "Debt against property value. SEBI caps it at 49%; above 35% is worth understanding."},
            {"name": "NAV per unit", "why": "Trading well below NAV can mean value — or that the market disputes the valuation."},
        ],
        "india_context": (
            "India's first REIT listed in 2019. The market is small — a handful of listed REITs — "
            "and dominated by commercial office space, with one retail-mall REIT. Minimum lot "
            "sizes have come down substantially, making them genuinely accessible to retail."
        ),
        "quiz": [
            {
                "question": "A REIT's distribution yield is 7%. Is that comparable to a 7% fixed deposit?",
                "options": [
                    "Yes, both pay 7% annually",
                    "No — the REIT distribution can fall and the unit price can drop",
                    "Yes, and the REIT is safer because it owns property",
                ],
                "answer": 1,
                "explanation": (
                    "An FD's 7% is contractual. A REIT's 7% is what the properties happened to "
                    "distribute last year — it moves with occupancy, rent renewals and interest "
                    "costs, and your capital fluctuates daily."
                ),
            },
            {
                "question": "Which single metric best warns you that distributions may fall?",
                "options": ["Unit price", "Occupancy rate", "Trading volume"],
                "answer": 1,
                "explanation": (
                    "Occupancy drives rent, rent drives distributions. It typically deteriorates "
                    "before the market prices it in."
                ),
            },
        ],
    },
    "invit": {
        "name": "InvITs — Infrastructure Investment Trusts",
        "one_liner": "Own operating infrastructure — highways, power lines — and receive the cash it throws off.",
        "level": "intermediate",
        "read_minutes": 6,
        "what_it_is": (
            "An InvIT holds operating infrastructure assets: toll roads, power transmission "
            "lines, gas pipelines, telecom towers. Revenue is typically contracted for years "
            "ahead — an availability-based transmission asset gets paid for being available, "
            "regardless of how much power flows. Like REITs, at least 90% of net distributable "
            "cash flow must be distributed."
        ),
        "how_you_earn": [
            "Regular distributions from contracted infrastructure revenue",
            "Capital appreciation if the trust acquires more assets accretively",
            "Inflation-linked tariff escalations in some concession agreements",
        ],
        "how_it_goes_wrong": [
            "The single biggest misunderstanding: many InvIT assets are finite-life concessions. "
            "A toll road with 18 years left on its concession returns to the government afterwards. "
            "Part of your 'yield' is therefore your own capital coming back, not pure income.",
            "Distinguish availability-based assets (paid regardless of usage — predictable) from "
            "traffic-based ones (toll roads, where a recession or a new parallel highway cuts revenue).",
            "Regulatory and political risk is real: tariff orders, concession disputes and toll "
            "policy changes are decided by bodies you cannot forecast.",
            "Leverage amplifies everything. SEBI caps it, but a highly levered InvIT in a rising-"
            "rate cycle sees distributions squeezed by interest costs.",
            "Trading volumes are thin. Treat these as hold-to-income, not tradeable positions.",
        ],
        "taxation_india": (
            "Same split treatment as REITs — interest, dividend and return-of-capital components "
            "taxed differently. The return-of-capital portion matters more here because of "
            "finite-life assets: it reduces your cost base and increases eventual capital gains."
        ),
        "who_its_for": "Income-focused investors with a 5-10 year horizon who want cash flows uncorrelated to equity markets.",
        "who_should_avoid": "Anyone who needs to exit quickly, or who reads the headline yield as a perpetual return.",
        "key_metrics": [
            {"name": "Distribution yield", "why": "Headline payout — but see 'residual concession life' before trusting it."},
            {"name": "Residual concession life", "why": "How many years the assets keep earning. Short life means part of the yield is capital return."},
            {"name": "Availability vs traffic-based", "why": "Availability-based revenue is contracted; traffic-based revenue is a forecast."},
            {"name": "Net debt / AUM", "why": "Leverage. Higher gearing means distributions are more rate-sensitive."},
            {"name": "Sponsor quality", "why": "The sponsor operates the assets and often sells more into the trust. Their incentives matter."},
        ],
        "india_context": (
            "India's InvIT market covers power transmission, roads and increasingly telecom "
            "infrastructure. Yields have generally run above REITs, reflecting the finite-life "
            "structure and lower liquidity rather than a free lunch."
        ),
        "quiz": [
            {
                "question": "An InvIT holding a toll road with 15 years of concession left offers a 10% yield. Why might that overstate the return?",
                "options": [
                    "Toll roads are always overvalued",
                    "Part of the distribution is your own capital returning as the asset winds down",
                    "The yield is quoted before tax",
                ],
                "answer": 1,
                "explanation": (
                    "With a finite concession, the asset is worth nothing at the end. Some of "
                    "each payment is return *of* capital, not return *on* capital — so the true "
                    "economic return is below the headline yield."
                ),
            },
        ],
    },
    "bond": {
        "name": "Corporate Bonds",
        "one_liner": "Lend to a company at a fixed coupon. The credit rating is the risk you're actually taking.",
        "level": "beginner",
        "read_minutes": 5,
        "what_it_is": (
            "A bond is a loan. You lend a company money; it pays periodic interest (the coupon) "
            "and returns the principal at maturity. Indian retail investors access these through "
            "listed bonds on NSE/BSE, online bond platforms, or bond ETFs and debt mutual funds."
        ),
        "how_you_earn": [
            "Coupon payments at a contractually fixed rate",
            "Principal returned at maturity if the issuer stays solvent",
            "Capital gains if you sell after interest rates fall (existing higher-coupon bonds become more valuable)",
        ],
        "how_it_goes_wrong": [
            "Credit risk is the one that actually loses money. A AAA bond and an A bond are not "
            "small variations on a theme — the extra 3% yield is compensation for a materially "
            "higher chance of not being repaid. India has seen AAA-rated issuers default.",
            "Interest-rate risk cuts the other way: if rates rise, your existing bond falls in "
            "price. Longer maturity means a bigger fall.",
            "Liquidity in Indian corporate bonds is genuinely poor. Selling before maturity often "
            "means accepting a worse price than the screen suggests.",
            "'Higher yield' is never free. If a bond yields far above G-Secs, the market is "
            "pricing real default risk — find out what it knows.",
            "Interest is taxed at your slab rate, so a 9% coupon in the 30% bracket is 6.3% net.",
        ],
        "taxation_india": (
            "Coupon interest is taxed at your income slab rate. Capital gains on sale before "
            "maturity are taxed by holding period. Because interest is slab-taxed, compare "
            "post-tax bond returns against post-tax alternatives, not headline rates."
        ),
        "who_its_for": "Investors wanting predictable income and lower volatility than equity, who can hold to maturity.",
        "who_should_avoid": "Anyone chasing the highest yield without reading the rating and the issuer's financials.",
        "key_metrics": [
            {"name": "Credit rating", "why": "AAA to D. The single most important field. Below AA, understand the issuer properly."},
            {"name": "Yield to maturity", "why": "Actual annualised return if held to maturity — not the coupon."},
            {"name": "Duration", "why": "Price sensitivity to rates. Duration 5 means roughly −5% if rates rise 1%."},
            {"name": "Spread over G-Sec", "why": "Yield above the risk-free rate. This is precisely the price of the credit risk."},
            {"name": "Secured vs unsecured", "why": "Secured bonds have a claim on assets if the issuer fails."},
        ],
        "india_context": (
            "SEBI has cut the minimum face value for listed bonds substantially, opening the "
            "market to retail. Bond ETFs (e.g. target-maturity PSU bond funds) offer "
            "diversified exposure with better liquidity than individual bonds."
        ),
        "quiz": [
            {
                "question": "Bond A (AAA) yields 7.2%. Bond B (A-) yields 11%. What explains the gap?",
                "options": [
                    "Bond B is a better investment",
                    "The market is pricing a materially higher chance Bond B defaults",
                    "Bond B has a longer maturity",
                ],
                "answer": 1,
                "explanation": (
                    "A 3.8% spread is the market's price for credit risk. You may judge it "
                    "mispriced — but you are being paid to take default risk, not handed free yield."
                ),
            },
        ],
    },
    "gsec": {
        "name": "Government Securities (G-Secs)",
        "one_liner": "Lend to the Government of India. No credit risk in rupees — but real price risk.",
        "level": "beginner",
        "read_minutes": 4,
        "what_it_is": (
            "Sovereign debt issued by the RBI on behalf of the government, from short treasury "
            "bills to 40-year bonds. Retail investors can buy directly through the RBI Retail "
            "Direct platform, or via gilt ETFs and funds."
        ),
        "how_you_earn": [
            "Half-yearly coupon interest",
            "Principal at maturity, effectively certain in rupee terms",
            "Capital gains if sold after yields fall",
        ],
        "how_it_goes_wrong": [
            "'Risk-free' means free of *credit* risk, not free of *price* risk. A 30-year G-Sec "
            "can lose 15% of its market value in a year if yields rise — that surprises people.",
            "Inflation is the quiet risk. A 7% coupon with 6% inflation is a 1% real return before tax.",
            "Interest is slab-taxed, so high earners keep noticeably less than the headline.",
            "Long-dated gilt funds are far more volatile than investors expect from 'government bonds'.",
        ],
        "taxation_india": "Interest taxed at slab rate. Capital gains by holding period. Sovereign Gold Bonds have their own separate treatment.",
        "who_its_for": "Anyone wanting the safest rupee asset, a portfolio ballast, or a matched maturity for a known future expense.",
        "who_should_avoid": "Investors who need to beat inflation meaningfully over long horizons using this alone.",
        "key_metrics": [
            {"name": "Yield to maturity", "why": "Your return if held to maturity."},
            {"name": "Maturity / duration", "why": "Longer means more price swing when rates move."},
            {"name": "Real yield", "why": "YTM minus expected inflation. This is what you actually gain in purchasing power."},
        ],
        "india_context": (
            "RBI Retail Direct lets individuals bid in government auctions with no intermediary. "
            "The 10-year G-Sec yield is the benchmark risk-free rate every other Indian asset is "
            "implicitly compared against."
        ),
        "quiz": [
            {
                "question": "Are G-Secs risk-free?",
                "options": [
                    "Yes, completely",
                    "Free of credit risk, but exposed to interest-rate and inflation risk",
                    "No, the government can default in rupees",
                ],
                "answer": 1,
                "explanation": (
                    "The government can always repay rupee debt, so credit risk is negligible. "
                    "But market price moves with rates, and inflation erodes the real return."
                ),
            },
        ],
    },
    "etf": {
        "name": "ETFs — Exchange Traded Funds",
        "one_liner": "A whole index in one trade, at a fraction of active-fund cost.",
        "level": "beginner",
        "read_minutes": 4,
        "what_it_is": (
            "An ETF holds a basket tracking an index and trades on the exchange like a share. "
            "Buying one NIFTYBEES unit gives you proportional exposure to all 50 Nifty "
            "constituents. Expense ratios are typically a fraction of active funds."
        ),
        "how_you_earn": ["The index's return, minus a small tracking difference", "Dividends, usually reinvested or distributed depending on the scheme"],
        "how_it_goes_wrong": [
            "Tracking error — the ETF drifts from the index it promises to follow. Check it.",
            "Thin liquidity in smaller ETFs means the market price can drift from NAV; you buy "
            "above fair value and sell below it. Always check the iNAV before trading.",
            "An index ETF is diversified across stocks but not across risk: a Nifty ETF is still "
            "~35% financials. You own one economy's large caps, not 'everything'.",
            "International ETFs periodically stop accepting inflows because of RBI's overseas "
            "investment limits, and can then trade at large premiums.",
        ],
        "taxation_india": "Equity ETFs taxed as equity. Gold, debt and international ETFs follow their underlying asset's rules — check before assuming.",
        "who_its_for": "Almost every investor as a core holding — cheap, transparent, instantly diversified.",
        "who_should_avoid": "Anyone who needs a specific outcome an index cannot provide.",
        "key_metrics": [
            {"name": "Expense ratio", "why": "Compounds against you every year. Lower is better, and the gap is large over decades."},
            {"name": "Tracking error", "why": "How faithfully it follows the index."},
            {"name": "AUM and daily volume", "why": "Small, illiquid ETFs trade at bad prices."},
            {"name": "iNAV vs market price", "why": "Buying at a premium to NAV is an instant, invisible loss."},
        ],
        "india_context": "Nifty 50, Nifty Next 50, Bank Nifty, gold, silver and Nasdaq-100 ETFs are all available on NSE with rupee-denominated access.",
        "quiz": [
            {
                "question": "An ETF's market price is 3% above its iNAV. What does that mean for a buyer?",
                "options": [
                    "The ETF is performing well",
                    "You'd pay 3% more than the underlying holdings are worth",
                    "Nothing — price and NAV always converge instantly",
                ],
                "answer": 1,
                "explanation": (
                    "A premium means paying above the value of what the fund holds. It usually "
                    "happens in illiquid or supply-constrained ETFs and can reverse sharply."
                ),
            },
        ],
    },
    "gold": {
        "name": "Gold",
        "one_liner": "A currency and crisis hedge, not a cash-flow asset.",
        "level": "beginner",
        "read_minutes": 4,
        "what_it_is": (
            "Financial gold exposure without storage or purity risk, via Gold ETFs, gold mutual "
            "funds, or Sovereign Gold Bonds where available. For Indian investors, gold is priced "
            "in dollars and converted — so returns combine the metal's move with the rupee's."
        ),
        "how_you_earn": [
            "Price appreciation of the metal",
            "Rupee depreciation against the dollar, which raises rupee gold prices independently",
            "For SGBs specifically, an additional fixed interest on the invested amount",
        ],
        "how_it_goes_wrong": [
            "Gold produces no cash flow. It cannot compound; it can only be revalued.",
            "It can stay flat or fall for many years at a time — long stretches of nothing are normal.",
            "The 'inflation hedge' story is unreliable over short periods; it is better understood "
            "as a hedge against currency debasement and crisis.",
            "Physical gold carries making charges and purity risk that ETFs simply don't.",
        ],
        "taxation_india": "Gold ETFs and funds taxed by holding period. Sovereign Gold Bonds have distinct treatment, including exemption on capital gains if held to maturity.",
        "who_its_for": "Most portfolios, as a 5-10% diversifier that behaves differently from equity in a crisis.",
        "who_should_avoid": "Anyone treating it as a growth asset or a substitute for income.",
        "key_metrics": [
            {"name": "Expense ratio", "why": "Matters more for an asset with no yield to offset costs."},
            {"name": "Tracking error vs spot", "why": "How closely the fund follows physical gold prices."},
            {"name": "Portfolio weight", "why": "Above ~15% you're taking a currency view, not diversifying."},
        ],
        "india_context": "Indian households hold enormous physical gold. If that's true for you, financial gold on top may be double-counting the same exposure.",
        "quiz": [
            {
                "question": "Why can rupee gold returns differ from dollar gold returns?",
                "options": [
                    "Indian gold is a different purity",
                    "The rupee's move against the dollar adds to or subtracts from the return",
                    "Import duty changes daily",
                ],
                "answer": 1,
                "explanation": (
                    "Gold is priced globally in dollars. If the rupee weakens, rupee gold rises "
                    "even with the dollar price flat — which is part of why it hedges Indian risk."
                ),
            },
        ],
    },
    "equity": {
        "name": "Equity — Direct Stocks",
        "one_liner": "Ownership in a business. The highest long-run return, and the deepest drawdowns.",
        "level": "beginner",
        "read_minutes": 5,
        "what_it_is": (
            "A share is a fractional claim on a company's future profits. Over long periods "
            "Indian equity has outpaced every other liquid asset class — while regularly falling "
            "30-50% along the way."
        ),
        "how_you_earn": ["Earnings growth reflected in the share price", "Dividends", "Re-rating, when the market pays a higher multiple for the same earnings"],
        "how_it_goes_wrong": [
            "Concentration. A handful of positions feels like a portfolio until one goes wrong.",
            "Sector correlation hiding in plain sight — four IT stocks is one bet, not four.",
            "Buying quality at any price. A great business at 90x earnings can go nowhere for years.",
            "Selling in drawdowns. The mathematical return of equity is only available to investors who stay invested through the falls.",
            "Confusing a falling price with a bargain without checking whether the business itself deteriorated.",
        ],
        "taxation_india": "Long-term gains (held over a year) taxed at 12.5% above the ₹1.25 lakh annual exemption; short-term at 20%. Dividends taxed at slab rate.",
        "who_its_for": "Investors with a 5+ year horizon who can tolerate large interim losses.",
        "who_should_avoid": "Money needed within three years, or anyone who would sell at −30%.",
        "key_metrics": [
            {"name": "ROE", "why": "How efficiently the business turns equity into profit. Consistently above 15% is a good sign."},
            {"name": "Debt-to-equity", "why": "Leverage kills companies in downturns."},
            {"name": "P/E vs growth", "why": "A high multiple is only justified by growth that actually arrives."},
            {"name": "Free cash flow", "why": "Profit is an opinion; cash is a fact."},
            {"name": "Promoter pledging", "why": "Pledged promoter shares are a specifically Indian red flag."},
        ],
        "india_context": "Over 5,000 listed companies across NSE and BSE, but liquidity and disclosure quality vary enormously outside the top 500.",
        "quiz": [
            {
                "question": "You hold TCS, Infosys, Wipro and HCL Tech. How many bets is that?",
                "options": ["Four — they're different companies", "Roughly one — Indian IT services", "Two"],
                "answer": 1,
                "explanation": (
                    "They share clients, currency exposure, and the same demand cycle. When "
                    "global IT budgets tighten they fall together. That's one exposure in four wrappers."
                ),
            },
        ],
    },
}

LEARNING_PATHS: dict[str, list[str]] = {
    "beginner": ["equity", "etf", "gsec", "gold"],
    "income_focused": ["gsec", "bond", "reit", "invit"],
    "diversifier": ["etf", "reit", "invit", "gold", "bond"],
    "complete": ["equity", "etf", "gsec", "bond", "gold", "reit", "invit"],
}


def list_topics() -> list[dict[str, Any]]:
    """Index of every lesson, without the body."""
    return [
        {
            "id": key,
            "name": entry["name"],
            "one_liner": entry["one_liner"],
            "level": entry["level"],
            "read_minutes": entry["read_minutes"],
            "quiz_questions": len(entry.get("quiz", [])),
        }
        for key, entry in CURRICULUM.items()
    ]


def get_topic(topic_id: str) -> dict[str, Any] | None:
    entry = CURRICULUM.get(topic_id.lower())
    if not entry:
        return None
    return {"id": topic_id.lower(), **entry}


def get_quiz(topic_id: str) -> list[dict[str, Any]]:
    """Quiz questions with the answers stripped."""
    entry = CURRICULUM.get(topic_id.lower())
    if not entry:
        return []
    return [
        {"index": i, "question": q["question"], "options": q["options"]}
        for i, q in enumerate(entry.get("quiz", []))
    ]


def grade_quiz(topic_id: str, answers: dict[Any, Any]) -> dict[str, Any]:
    """Grade quiz answers, returning the explanation for every question."""
    entry = CURRICULUM.get(topic_id.lower())
    if not entry:
        raise ValueError(f"Unknown topic '{topic_id}'")

    questions = entry.get("quiz", [])
    results = []
    correct = 0

    for index, question in enumerate(questions):
        given = answers.get(index, answers.get(str(index)))  # tolerate string keys from JSON
        try:
            given = int(given) if given is not None else None
        except (TypeError, ValueError):
            given = None

        is_correct = given == question["answer"]
        correct += int(is_correct)
        results.append(
            {
                "index": index,
                "question": question["question"],
                "your_answer": question["options"][given] if given is not None and 0 <= given < len(question["options"]) else None,
                "correct_answer": question["options"][question["answer"]],
                "correct": is_correct,
                "explanation": question["explanation"],
            }
        )

    return {
        "topic": topic_id.lower(),
        "score": correct,
        "total": len(questions),
        "percent": round(correct / len(questions) * 100) if questions else 0,
        "results": results,
    }


def learning_path(profile: str | None = None, holdings_classes: list[str] | None = None) -> dict[str, Any]:
    """
    Suggest what to learn next, based on the profile and what's *missing*.

    Ordering by absence is deliberate: someone already holding equity gains
    more from understanding REITs than from another equity lesson.
    """
    held = set(holdings_classes or [])

    if profile in ("conservative", "moderately_conservative"):
        path = LEARNING_PATHS["income_focused"]
    elif profile in ("growth", "aggressive"):
        path = LEARNING_PATHS["diversifier"]
    else:
        path = LEARNING_PATHS["complete"]

    ordered = [t for t in path if t not in held] + [t for t in path if t in held]

    return {
        "profile": profile,
        "path": [
            {
                **{
                    k: v
                    for k, v in (get_topic(t) or {}).items()
                    if k in ("id", "name", "one_liner", "level", "read_minutes")
                },
                "already_held": t in held,
                "reason": (
                    "You hold this — worth understanding the failure modes"
                    if t in held
                    else "Not in your portfolio — this is the gap"
                ),
            }
            for t in ordered
            if get_topic(t)
        ],
    }


def explain_for_portfolio(topic_id: str, portfolio_context: str, register: str = "screen") -> dict[str, Any]:
    """
    Personalise a lesson against the user's actual holdings.

    The factual content stays static — the model only connects it to what the
    person owns, so it cannot invent a tax rule or a product feature.
    """
    from ai.brain import brain
    from ai.providers import ProviderError

    entry = get_topic(topic_id)
    if not entry:
        raise ValueError(f"Unknown topic '{topic_id}'")

    prompt = f"""The investor is learning about {entry['name']}.

VERIFIED FACTS (do not contradict these, do not add new tax or regulatory claims):
{entry['what_it_is']}

How it goes wrong:
{chr(10).join(f'- {r}' for r in entry['how_it_goes_wrong'])}

THEIR ACTUAL PORTFOLIO:
{portfolio_context}

Explain what this asset class would mean *for this specific portfolio*. Would it \
diversify what they hold or duplicate it? What would it change about their risk? \
Be concrete and reference their actual positions. If it would be a poor fit, say so."""

    try:
        response = brain.think(prompt, task="education", register=register, max_tokens=700)
        return {"topic": topic_id, "explanation": response.text, "meta": response.as_dict()}
    except ProviderError as exc:
        return {
            "topic": topic_id,
            "explanation": None,
            "error": str(exc),
            "fallback": entry["who_its_for"],
        }

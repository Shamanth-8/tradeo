import { useCallback, useEffect, useState } from 'react'
import { ShieldAlert, ShieldCheck } from 'lucide-react'
import { HudPanel, Loading, Stat } from './hud/HudPanel'
import { autopilotApi } from '../services/api'

/**
 * Account-level risk: the limits every agent's buy must pass (autopilot/risk.py).
 * Shows where the account stands against each one, lets you change them, and
 * resume after the drawdown switch has halted buying.
 */

const FIELDS = [
    { key: 'risk_per_trade_pct', label: 'Risk per trade', hint: 'lost if the stop is hit', step: 0.05 },
    { key: 'daily_loss_limit_pct', label: 'Daily loss limit', hint: 'no new buys today past this', step: 0.5 },
    { key: 'max_drawdown_pct', label: 'Drawdown switch', hint: 'halts buys until you resume', step: 1 },
    { key: 'max_sector_pct', label: 'Sector cap', hint: 'of the account in one sector', step: 5 },
]

const pct = (v) => `${v > 0 ? '+' : ''}${Number(v || 0).toFixed(2)}%`

export default function RiskPanel() {
    const [risk, setRisk] = useState(null)
    const [draft, setDraft] = useState({})
    const [note, setNote] = useState(null)
    const [busy, setBusy] = useState(false)

    const load = useCallback(() => {
        autopilotApi.risk().then(({ data }) => setRisk(data)).catch((err) => setNote(err.message))
    }, [])
    useEffect(load, [load])

    const save = async (fn, message) => {
        setBusy(true)
        try {
            const { data } = await fn()
            setRisk(data)
            setDraft({})
            setNote(message)
        } catch (err) {
            setNote(err?.response?.data?.detail || err.message)
        } finally {
            setBusy(false)
        }
    }

    if (!risk) return <Loading rows={2} label="Account risk" />

    const s = { ...risk.settings, ...draft }
    const sectors = Object.entries(risk.sectors || {}).sort((a, b) => b[1] - a[1]).slice(0, 6)
    const market = risk.market || {}

    return (
        <HudPanel
            title="Account risk"
            subtitle="Every agent's buy must pass these, on top of its own rules. Positions are sized so a stop-out loses the risk per trade, capped by each agent's maximum size."
        >
            {risk.halted && (
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded border border-danger-400/40 bg-danger-500/10 px-3 py-2">
                    <p className="flex items-center gap-2 text-xs text-danger-300">
                        <ShieldAlert className="h-4 w-4" /> Buys halted: {risk.reason}
                    </p>
                    <button disabled={busy} className="btn-secondary text-xs"
                        onClick={() => save(autopilotApi.resumeRisk, 'Buys resumed; the peak now starts from today.')}>
                        Resume buys
                    </button>
                </div>
            )}

            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <Stat label="Today" value={pct(risk.today_pct)} tone={risk.today_pct < 0 ? 'down' : 'up'}
                    sub={`limit −${risk.settings.daily_loss_limit_pct}%`} />
                <Stat label="From peak" value={pct(risk.drawdown_pct)} tone={risk.drawdown_pct < 0 ? 'down' : 'neutral'}
                    sub={`switch at −${risk.settings.max_drawdown_pct}%`} />
                <Stat label="Market filter" value={!risk.settings.market_filter ? 'Off' : market.ok ? 'Buying' : market.ok === false ? 'Paused' : 'No data'}
                    tone={market.ok ? 'up' : 'alert'} sub={market.detail} mono={false} />
                <div>
                    <div className="hud-label">Largest sectors</div>
                    {sectors.length ? sectors.map(([name, v]) => (
                        <div key={name} className="flex justify-between text-xs text-dark-300">
                            <span className="truncate">{name.replace('unclassified:', '')}</span>
                            <span className={v > risk.settings.max_sector_pct ? 'text-danger-400' : ''}>{v.toFixed(1)}%</span>
                        </div>
                    )) : <p className="text-xs text-dark-500">No open positions</p>}
                </div>
            </div>

            <div className="mt-4 grid gap-3 border-t border-dark-800/60 pt-4 text-xs sm:grid-cols-2 lg:grid-cols-5">
                {FIELDS.map(({ key, label, hint, step }) => {
                    const [low, high] = risk.limits[key]
                    return (
                        <label key={key} className="space-y-1">
                            <span className="block text-dark-300">{label}: {s[key]}% <span className="text-dark-500">· {hint}</span></span>
                            <input type="range" min={low} max={high} step={step} value={s[key]} className="w-full"
                                onChange={(e) => setDraft((d) => ({ ...d, [key]: Number(e.target.value) }))} />
                        </label>
                    )
                })}
                <label className="flex items-center gap-2 text-dark-300">
                    <input type="checkbox" checked={!!s.market_filter}
                        onChange={(e) => setDraft((d) => ({ ...d, market_filter: e.target.checked }))} />
                    Only buy while Nifty 50 is above its 200-day average
                </label>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
                {Object.keys(draft).length > 0 && (
                    <button disabled={busy} className="btn-primary text-xs"
                        onClick={() => save(() => autopilotApi.updateRisk(draft), 'Risk limits saved.')}>
                        <ShieldCheck className="h-3.5 w-3.5" /> Save limits
                    </button>
                )}
                {note && <p className="text-xs text-dark-300">{note}</p>}
            </div>
        </HudPanel>
    )
}

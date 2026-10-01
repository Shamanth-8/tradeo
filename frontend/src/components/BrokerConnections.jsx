import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, ExternalLink, LogIn, Plug, XCircle } from 'lucide-react'
import { HudPanel } from './hud/HudPanel'
import { API_ROOT, wealthApi } from '../services/api'

/**
 * One card per broker, instead of 20+ fields in one list.
 *
 * Fields are grouped by their prefix (ZERODHA_…, DHAN_…) against the broker
 * list from the backend, so plugin brokers get a card automatically.
 * `renderField` and `renderTest` come from the Connections page, which owns
 * the drafts and the save button.
 */

const PREFIX = { angelone: 'ANGELONE_', dhan: 'DHAN_', zerodha: 'ZERODHA_', kotak: 'KOTAK_' }

export default function BrokerConnections({ fields, renderField, renderTest, focus }) {
    const [brokers, setBrokers] = useState([])
    const ref = useRef(null)

    useEffect(() => {
        wealthApi.brokers().then(({ data }) => setBrokers(data.brokers || [])).catch(() => {})
    }, [])

    useEffect(() => {
        if (focus) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, [focus, brokers.length])

    const live = brokers.filter((b) => !['manual', 'depository'].includes(b.broker))
    const prefixOf = (b) => PREFIX[b.broker] || `${b.broker.toUpperCase()}_`
    const general = fields.filter((f) => !live.some((b) => f.key.startsWith(prefixOf(b))))

    return (
        <div ref={ref} className="space-y-3">
            <HudPanel
                title="Brokers"
                subtitle="Optional. Without a broker Tradeo paper trades on free Yahoo Finance prices. Connect one to see your real holdings; live orders stay off unless you enable them."
                right={<Plug className="h-4 w-4 text-primary-400/60" />}
            >
                {general.length > 0 && <div className="space-y-3">{general.map(renderField)}</div>}
            </HudPanel>

            <div className="grid gap-3 lg:grid-cols-2">
                {live.map((b) => {
                    const own = fields.filter((f) => f.key.startsWith(prefixOf(b)))
                    return (
                        <HudPanel
                            key={b.broker}
                            title={b.display_name}
                            corners={false}
                            right={
                                b.connected ? (
                                    <span className="flex items-center gap-1 text-[11px] text-success-400"><CheckCircle2 className="h-3.5 w-3.5" /> connected</span>
                                ) : b.configured ? (
                                    <span className="flex items-center gap-1 text-[11px] text-danger-400" title={b.error || ''}><XCircle className="h-3.5 w-3.5" /> login failed</span>
                                ) : (
                                    <span className="text-[11px] text-dark-500">not connected</span>
                                )
                            }
                        >
                            <div className="space-y-3">
                                <div className="flex flex-wrap gap-3 text-[11px]">
                                    {b.docs_url && (
                                        <a href={b.docs_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-primary-300 hover:underline">
                                            <ExternalLink className="h-3 w-3" /> Get API keys
                                        </a>
                                    )}
                                    {b.login_url && (
                                        <a href={`${API_ROOT}${b.login_url}`} target="_blank" rel="noreferrer"
                                            className="flex items-center gap-1 text-primary-300 hover:underline">
                                            <LogIn className="h-3 w-3" /> Daily login
                                        </a>
                                    )}
                                    <span className={b.trading_allowed ? 'text-alert-300' : 'text-dark-500'}>
                                        live orders {b.trading_allowed ? 'ALLOWED' : 'off'}
                                    </span>
                                </div>
                                {own.length ? own.map(renderField) : (
                                    <p className="text-[11px] text-dark-500">No settings declared.</p>
                                )}
                                {renderTest(b)}
                            </div>
                        </HudPanel>
                    )
                })}
            </div>
            <p className="px-1 text-[11px] text-dark-500">
                Another broker? Add a plugin file — see backend/brokers/plugins/README.md. It appears here automatically.
            </p>
        </div>
    )
}

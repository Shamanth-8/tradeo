/** Small form controls shared by the research lab screens. */

export function Segmented({ value, onChange, options }) {
    return (
        <div className="flex flex-wrap gap-0.5 rounded-md border border-dark-800 bg-dark-950/60 p-0.5">
            {options.map((o) => (
                <button
                    type="button"
                    key={o.id}
                    onClick={() => onChange(o.id)}
                    className={`press rounded px-2.5 py-1 text-xs transition-colors ${
                        value === o.id ? 'bg-primary-500/20 text-primary-200' : 'text-dark-400 hover:text-dark-100'
                    }`}
                >
                    {o.label}
                </button>
            ))}
        </div>
    )
}

export function Chip({ active, onClick, children }) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={`press rounded-full border px-2.5 py-0.5 text-[11px] transition-all ${
                active
                    ? 'border-primary-400/50 bg-primary-500/15 text-primary-200'
                    : 'border-dark-700 text-dark-400 hover:border-dark-500 hover:text-dark-100'
            }`}
        >
            {children}
        </button>
    )
}

export function Field({ label, children }) {
    return (
        <label className="block">
            <span className="hud-label mb-1 block">{label}</span>
            {children}
        </label>
    )
}

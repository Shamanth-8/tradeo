/**
 * Small, safe markdown for agent replies: headings, bold, code, lists and
 * tables. Everything is escaped before any markup is added, so nothing the
 * model writes can inject HTML.
 */

function escapeHtml(text) {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
}

function inline(text) {
    return text
        .replace(/`([^`]+)`/g, '<code class="rounded bg-dark-950/70 px-1 font-mono text-[0.85em] text-primary-200">$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong class="text-dark-50">$1</strong>')
        .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
}

function table(lines) {
    const rows = lines
        .filter((l) => !/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(l))
        .map((l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => inline(c.trim())))
    if (!rows.length) return ''
    const [head, ...body] = rows
    return (
        '<div class="my-2 overflow-x-auto"><table class="w-full text-xs">' +
        `<thead><tr>${head.map((c) => `<th class="border-b border-dark-700 px-2 py-1 text-left font-mono text-[10px] uppercase tracking-wider text-dark-400">${c}</th>`).join('')}</tr></thead>` +
        `<tbody>${body.map((r) => `<tr class="border-b border-dark-800/70">${r.map((c) => `<td class="px-2 py-1 font-mono">${c}</td>`).join('')}</tr>`).join('')}</tbody>` +
        '</table></div>'
    )
}

export function renderMarkdown(source) {
    if (!source) return ''
    const out = []
    const blocks = escapeHtml(source).split(/```/)
    blocks.forEach((block, i) => {
        if (i % 2 === 1) {
            const code = block.replace(/^[a-z0-9]*\n/i, '')
            out.push(`<pre class="my-2 overflow-x-auto rounded-md border border-dark-800 bg-dark-950/80 p-3 font-mono text-xs text-dark-200">${code}</pre>`)
            return
        }
        const lines = block.split('\n')
        let list = null
        let tbl = []
        const flush = () => {
            if (list) {
                out.push(`<${list.tag} class="my-1 ml-5 ${list.tag === 'ol' ? 'list-decimal' : 'list-disc'} space-y-0.5">${list.items.join('')}</${list.tag}>`)
                list = null
            }
            if (tbl.length) {
                out.push(table(tbl))
                tbl = []
            }
        }
        for (const line of lines) {
            if (/^\s*\|.*\|\s*$/.test(line)) {
                if (list) { const t = tbl; tbl = []; flush(); tbl = t }
                tbl.push(line)
                continue
            }
            if (tbl.length) flush()
            const heading = line.match(/^(#{1,4})\s+(.+)$/)
            const bullet = line.match(/^\s*[-*•]\s+(.+)$/)
            const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/)
            if (heading) {
                flush()
                const size = heading[1].length <= 2 ? 'text-base' : 'text-sm'
                out.push(`<div class="mt-3 mb-1 font-semibold ${size} text-primary-200">${inline(heading[2])}</div>`)
            } else if (bullet || numbered) {
                const tag = bullet ? 'ul' : 'ol'
                if (list && list.tag !== tag) flush()
                list = list || { tag, items: [] }
                list.items.push(`<li>${inline((bullet || numbered)[1])}</li>`)
            } else if (!line.trim()) {
                flush()
                out.push('<div class="h-2"></div>')
            } else {
                flush()
                out.push(`<div>${inline(line)}</div>`)
            }
        }
        flush()
    })
    return out.join('')
}

export default function Markdown({ children, className = '' }) {
    return (
        <div
            className={`text-sm leading-relaxed ${className}`}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(children) }}
        />
    )
}

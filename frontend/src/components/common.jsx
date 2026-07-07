// Small shared presentational helpers.

// Material Symbols icon (replaces the original micon() string helper).
export function MIcon({ name, size, style }) {
  return (
    <span className="material-symbols-outlined" style={{ ...(size ? { fontSize: size } : {}), ...style }}>
      {name}
    </span>
  )
}

// Highlight the matched substring in bold (replaces the original hl() helper).
export function Highlight({ text, query }) {
  const t = String(text ?? '')
  if (!query) return <>{t}</>
  const esc = query.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&')
  const parts = t.split(new RegExp(`(${esc})`, 'gi'))
  return (
    <>
      {parts.map((s, i) =>
        s.toLowerCase() === query.toLowerCase() ? <strong key={i}>{s}</strong> : <span key={i}>{s}</span>,
      )}
    </>
  )
}

const SEND_PATH = 'M2 21L23 12 2 3v7l15 2-15 2z'
export function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="white">
      <path d={SEND_PATH} />
    </svg>
  )
}

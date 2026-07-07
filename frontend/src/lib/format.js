// Date / clip formatting helpers — ported 1:1 from the original DVR_frontend.html.

const UTC_DT = { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }

export function friendlyTripLabel(t) {
  return `${t.driverName || 'Trip'} · ${t.assetId || ''}`.trim()
}

export function fmtUtcDateTime(iso) {
  return iso ? new Date(iso).toLocaleString('en-GB', UTC_DT) : ''
}

// FIX 4 helpers ------------------------------------------------------------
export function fmtClip(iso) {
  if (!iso) return '—'
  return String(iso).replace('T', ' ').replace(/\.\d+/, '').replace(/\+.*$/, '')
}

export function computeClipEnd(startIso, durMin) {
  if (!startIso) return '—'
  const start = new Date(startIso)
  if (isNaN(start.getTime())) return fmtClip(startIso) // unparseable — show raw rather than throw
  const end = new Date(start.getTime() + durMin * 60000)
  if (isNaN(end.getTime())) return fmtClip(startIso)
  return end.toISOString().replace('T', ' ').replace(/\.\d+Z$/, '')
}

// Category → Material Symbol name (shared by chips, dropdown rows, icons).
export const CATEGORY_ICON = {
  Drivers: 'person',
  Assets: 'local_shipping',
  Trips: 'route',
  'Event Types': 'warning',
  DateRange: 'calendar_month',
}

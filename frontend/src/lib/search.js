// Autocomplete search over the loaded fleet data.
// Ported from execGlobalSearch / executeLocalSearchFilter / getTripPickerSource.
import { friendlyTripLabel, fmtUtcDateTime } from './format.js'

export function getTripPickerSource(currentTrips, fleetData) {
  if (currentTrips.length > 0) {
    return currentTrips.map((t) => ({
      label: friendlyTripLabel(t),
      sub: fmtUtcDateTime(t.startTimeUTC),
      raw: t.tripId,
    }))
  }
  return (fleetData.trip_ids || []).map((t) => ({ label: String(t), sub: '', raw: t }))
}

// Global search: returns groups keyed by category, each an array of matches.
export function globalSearch(fleetData, currentTrips, query) {
  const ql = query.toLowerCase()
  const groups = { Drivers: [], Assets: [], Trips: [], 'Event Types': [] }

  ;(fleetData.drivers || []).forEach((d) => {
    if ((d.driverName || '').toLowerCase().includes(ql) || (d.driverId || '').toLowerCase().includes(ql)) {
      groups.Drivers.push({ label: d.driverName, sub: d.driverId, raw: d })
    }
  })
  ;(fleetData.asset_ids || []).forEach((a) => {
    if (String(a).toLowerCase().includes(ql)) groups.Assets.push({ label: String(a), sub: '', raw: a })
  })
  getTripPickerSource(currentTrips, fleetData).forEach((t) => {
    if (t.label.toLowerCase().includes(ql)) groups.Trips.push(t)
  })
  ;(fleetData.events || []).forEach((e) => {
    if (String(e).toLowerCase().includes(ql)) groups['Event Types'].push({ label: String(e), sub: '', raw: e })
  })

  const count = Object.values(groups).reduce((n, arr) => n + arr.length, 0)
  return { groups, count }
}

// Local (category-scoped) search: returns a flat list of matches.
export function localSearch(fleetData, currentTrips, cat, query) {
  const ql = query.toLowerCase()
  const m = []
  if (cat === 'Drivers') {
    ;(fleetData.drivers || []).forEach((d) => {
      if (!ql || (d.driverName || '').toLowerCase().includes(ql) || (d.driverId || '').toLowerCase().includes(ql))
        m.push({ label: d.driverName, sub: d.driverId, raw: d })
    })
  } else if (cat === 'Assets') {
    ;(fleetData.asset_ids || []).forEach((a) => {
      if (!ql || String(a).toLowerCase().includes(ql)) m.push({ label: String(a), sub: '', raw: a })
    })
  } else if (cat === 'Trips') {
    getTripPickerSource(currentTrips, fleetData).forEach((t) => {
      if (!ql || t.label.toLowerCase().includes(ql)) m.push(t)
    })
  } else if (cat === 'Event Types') {
    ;(fleetData.events || []).forEach((e) => {
      if (!ql || String(e).toLowerCase().includes(ql)) m.push({ label: String(e), sub: '', raw: e })
    })
  }
  return m
}

// Build the collectedItems entry for a staged match in a given category.
export function stageEntry(cat, match) {
  if (cat === 'Drivers') return { option: cat, selectedItem: match.raw }
  if (cat === 'Assets') return { option: cat, selectedItem: { assetId: match.raw } }
  if (cat === 'Trips') return { option: cat, selectedItem: { tripId: match.raw, label: match.label } }
  return { option: cat, selectedItem: { event_type: match.raw } }
}

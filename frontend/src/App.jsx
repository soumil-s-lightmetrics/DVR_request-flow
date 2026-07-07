import { useCallback, useMemo, useRef, useState } from 'react'
import TopBar from './components/TopBar.jsx'
import Landing from './components/Landing.jsx'
import MessagesArea from './components/MessagesArea.jsx'
import ChatInputBar from './components/ChatInputBar.jsx'
import CanvasPanel from './components/CanvasPanel.jsx'
import { useWebSocket } from './hooks/useWebSocket.js'
import { friendlyTripLabel } from './lib/format.js'

const EMPTY_FLEET = { drivers: [], asset_ids: [], trip_ids: [], events: [], fleet_id: null }

export default function App() {
  const [fleetData, setFleetData] = useState(EMPTY_FLEET)
  const [messages, setMessages] = useState([])
  const [typing, setTyping] = useState(false)
  const [view, setView] = useState('landing') // 'landing' | 'chat'
  const [collectedItems, setCollectedItems] = useState([])
  const [currentTrips, setCurrentTrips] = useState([])
  const [summary, setSummary] = useState('')
  const [graphPaused, setGraphPaused] = useState(false)
  const [threadId, setThreadId] = useState(() => 't_' + Date.now())
  const [highlight, setHighlight] = useState({ tripId: null, n: 0 })
  const [chatSeed, setChatSeed] = useState({ text: '', n: 0 })

  // Cross-event bookkeeping that must survive renders without triggering them.
  const idRef = useRef(0)
  const currentConfirmParamsRef = useRef(null)
  const lastDvrRequestDetailsRef = useRef(null)

  // Synchronous mirror of collectedItems. The original kept chips in a plain
  // mutable array, so flows like "stage a trip then immediately send" (the
  // single-result auto path) read the update within the same event. React
  // state is async, so we mirror it here and read the ref in send paths.
  const collectedRef = useRef([])
  const commitCollected = useCallback((next) => {
    collectedRef.current = next
    setCollectedItems(next)
  }, [])

  const nextId = () => 'm' + ++idRef.current

  // ── message log helpers ──────────────────────────────────────────────
  const addMsg = useCallback((msg) => setMessages((m) => [...m, { id: nextId(), ...msg }]), [])
  const removeMsg = useCallback((id) => setMessages((m) => m.filter((x) => x.id !== id)), [])
  const appendUser = useCallback((text) => addMsg({ kind: 'user', text }), [addMsg])
  const appendBot = useCallback((text) => addMsg({ kind: 'bot', text }), [addMsg])

  // ── incoming server messages (ported from handleServerMessage) ───────
  // A fresh closure each render — useWebSocket keeps the latest in a ref.
  function handleServerMessage(msg) {
    setTyping(false)

    if (msg.type === 'load_complete') return

    if (msg.type === 'error') {
      appendBot('Error: ' + (msg.message || 'Something went wrong.'))
      return
    }

    if (msg.type === 'interrupt') {
      const p = msg.payload
      if (p.message === 'please provide timestamp') {
        addMsg({ kind: 'ts-interrupt' })
        return
      }
      if (p.message === 'show_results') {
        setGraphPaused(true)
        if (p.first) {
          if (p.filters) applyBackendFilterChips(p.filters)
          renderTripResults(p.trips, p.summary)
        }
        return
      }
      if (p.message === 'confirm_dvr') {
        currentConfirmParamsRef.current = p.params
        addMsg({ kind: 'confirm-dvr', payload: p })
        return
      }
      return
    }

    if (msg.type === 'chat_response') {
      if (msg.more === false) setGraphPaused(false)
      const r = msg.response
      if (r.uploadRequestId) renderSuccess(r.uploadRequestId, r.dvr_summary)
      else if (r.chat_response) appendBot(r.chat_response)
      return
    }
  }

  const { send, connected } = useWebSocket(handleServerMessage)

  // ── fleet loader ──────────────────────────────────────────────────────
  const loadFleet = useCallback(
    async (fid) => {
      const res = await fetch(`/${fid}/load-data`)
      if (!res.ok) throw new Error('Bad response')
      const d = await res.json()
      const next = {
        drivers: d.drivers || [],
        asset_ids: d.asset_ids || [],
        trip_ids: d.trip_ids || [],
        events: d.events || [],
        fleet_id: fid,
      }
      setFleetData(next)
      send({ type: 'load_data', fleet_data: { ...next, fleet_id: fid }, thread_id: threadId })
    },
    [send, threadId],
  )

  // ── backend-driven filter chips ───────────────────────────────────────
  function applyBackendFilterChips(filters) {
    const items = []
    if (filters.driver)
      items.push({ option: 'Drivers', selectedItem: { driverId: filters.driver.driverId, driverName: filters.driver.driverName } })
    if (filters.asset) items.push({ option: 'Assets', selectedItem: { assetId: filters.asset } })
    if (filters.events && filters.events.length)
      filters.events.forEach((ev) => items.push({ option: 'Event Types', selectedItem: { event_type: ev } }))
    // Guard against invalid/epoch timestamps so a bogus "1 January 1970" chip never renders.
    if (filters.date_range && filters.date_range.start && filters.date_range.end) {
      const s = new Date(filters.date_range.start)
      const e = new Date(filters.date_range.end)
      const validYear = (d) => !isNaN(d.getTime()) && d.getFullYear() > 1971
      if (validYear(s) && validYear(e)) {
        const fmt = (d) => d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
        items.push({
          option: 'DateRange',
          selectedItem: { label: `${fmt(s)} – ${fmt(e)}`, start: filters.date_range.start, end: filters.date_range.end },
        })
      }
    }
    commitCollected(items)
  }

  // ── trip results + success ────────────────────────────────────────────
  function renderTripResults(trips, sum) {
    setCurrentTrips(trips)
    setSummary(sum || '')
    appendBot((sum ? sum + ' ' : '') + 'Results are shown in the panel.')
    if (trips.length > 0) {
      appendBot('Would you like to raise a footage request for any of these trips?')
      addMsg({ kind: 'action-chips' })
    }
  }

  function renderSuccess(id, sum) {
    const details = lastDvrRequestDetailsRef.current
    addMsg({ kind: 'success', id2: id, details, summary: sum })
    lastDvrRequestDetailsRef.current = null
    currentConfirmParamsRef.current = null
    clearSelectedTrip()
  }

  // ── outbound query helpers ────────────────────────────────────────────
  function buildTagCtx(items) {
    return items
      .filter((e) => e.option !== 'DateRange')
      .map((e) => {
        if (e.option === 'Drivers') return `[Driver: ${e.selectedItem.driverName || e.selectedItem.driverId}]`
        if (e.option === 'Assets') return `[Asset: ${e.selectedItem.assetId}]`
        if (e.option === 'Trips') return `[Trip: ${e.selectedItem.tripId}]`
        if (e.option === 'Event Types') return `[Event: ${e.selectedItem.event_type}]`
        return ''
      })
      .filter(Boolean)
      .join(' ')
  }

  function buildActiveFilters() {
    const items = collectedRef.current
    const driverEntry = items.find((e) => e.option === 'Drivers')
    const assetEntry = items.find((e) => e.option === 'Assets')
    const eventEntries = items.filter((e) => e.option === 'Event Types')
    const dateEntry = items.find((e) => e.option === 'DateRange')
    return {
      driver: driverEntry ? { driverId: driverEntry.selectedItem.driverId, driverName: driverEntry.selectedItem.driverName } : null,
      asset: assetEntry ? assetEntry.selectedItem.assetId : null,
      events: eventEntries.length ? eventEntries.map((e) => e.selectedItem.event_type) : null,
      date_range: dateEntry ? { start: dateEntry.selectedItem.start, end: dateEntry.selectedItem.end } : null,
    }
  }

  function dispatch(pt) {
    const items = collectedRef.current
    if (items.length > 0) {
      const fp = { query: pt, fleet_id: fleetData.fleet_id, query_type: 'directed' }
      items.forEach((e) => {
        if (e.option === 'Drivers') fp.selectedItem = e.selectedItem
        else if (e.option === 'Assets') fp.selectedItem = { assetId: e.selectedItem.assetId }
        else if (e.option === 'Trips') fp.selectedItem = { tripId: e.selectedItem.tripId }
        else if (e.option === 'Event Types') fp.selectedItem = { event_type: e.selectedItem.event_type }
        if (e.option !== 'DateRange') fp.option = e.option
      })
      send({ type: 'autocomplete_result', ...fp, thread_id: threadId })
    } else {
      send({ type: 'only_query', query: pt, thread_id: threadId, fleet_id: fleetData.fleet_id })
    }
  }

  // ── send handlers passed to the search pills ─────────────────────────
  function landingSend(text) {
    const tc = buildTagCtx(collectedRef.current)
    const fq = (tc ? tc + ' ' : '') + text.trim()
    if (!fq.trim()) return false
    setView('chat')
    appendUser(fq)
    setTyping(true)
    dispatch(fq)
    return true
  }

  function chatSend(text) {
    const t = text.trim()
    if (!t) return false
    const tc = buildTagCtx(collectedRef.current)
    const fq = (tc ? tc + ' ' : '') + t
    if (graphPaused) {
      appendUser(fq)
      setTyping(true)
      const tripEntry = collectedRef.current.find((e) => e.option === 'Trips')
      send({
        type: 'resume_graph',
        thread_id: threadId,
        resume_value: { text: fq, tripId: tripEntry ? tripEntry.selectedItem.tripId : null, activeFilters: buildActiveFilters() },
      })
    } else {
      appendUser(fq)
      setTyping(true)
      dispatch(fq)
    }
    return true
  }

  // ── chip / trip selection ─────────────────────────────────────────────
  const onStage = useCallback((entry) => commitCollected([...collectedRef.current, entry]), [commitCollected])
  const onRemoveChip = useCallback(
    (idx) => commitCollected(collectedRef.current.filter((_, i) => i !== idx)),
    [commitCollected],
  )

  const onHighlightTripChip = useCallback((idx) => {
    const entry = collectedRef.current[idx]
    if (!entry || entry.option !== 'Trips') return
    setHighlight((h) => ({ tripId: entry.selectedItem.tripId, n: h.n + 1 }))
  }, [])

  function setTripChip(trip) {
    commitCollected([
      ...collectedRef.current.filter((e) => e.option !== 'Trips'),
      { option: 'Trips', selectedItem: { tripId: trip.tripId, label: friendlyTripLabel(trip) } },
    ])
    setHighlight((h) => ({ tripId: trip.tripId, n: h.n + 1 }))
  }

  function clearSelectedTrip() {
    commitCollected(collectedRef.current.filter((e) => e.option !== 'Trips'))
  }

  // ── DVR request flows ─────────────────────────────────────────────────
  function sendDvrRequestForTrip(trip, type) {
    const label = type === 'clip' ? 'DVR clip' : 'timelapse'
    const s = trip.startTimeUTC
      ? new Date(trip.startTimeUTC).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })
      : ''
    chatSend(`Request a ${label} from ${s}`)
  }

  function onStartDvr(type) {
    const tripEntry = collectedRef.current.find((e) => e.option === 'Trips')
    const selectedTrip = tripEntry ? currentTrips.find((t) => t.tripId === tripEntry.selectedItem.tripId) : null
    if (selectedTrip) {
      sendDvrRequestForTrip(selectedTrip, type)
    } else if (currentTrips.length === 1) {
      setTripChip(currentTrips[0])
      sendDvrRequestForTrip(currentTrips[0], type)
    } else {
      const label = type === 'clip' ? 'DVR clip' : 'timelapse'
      setChatSeed((s) => ({ text: `Request a ${label} — `, n: s.n + 1 }))
    }
  }

  function onUseTrip(idx) {
    const trip = currentTrips[idx]
    if (!trip) return
    setTripChip(trip)
    setMessages((m) => [
      ...m.filter((x) => x.kind !== 'trip-type-prompt'),
      { id: nextId(), kind: 'bot', text: `Selected ${trip.assetId} · ${trip.driverName || ''} — what would you like to request?` },
      { id: nextId(), kind: 'trip-type-prompt' },
    ])
  }

  // ── interrupt handlers ────────────────────────────────────────────────
  function onSubmitTimestamp(start, end, msgId) {
    const startIso = new Date(start).toISOString()
    const endIso = new Date(end).toISOString()
    const fmt = (v) => new Date(v).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
    const label = `${fmt(start)} – ${fmt(end)}`
    commitCollected([...collectedRef.current, { option: 'DateRange', selectedItem: { label, start: startIso, end: endIso } }])
    send({ type: 'resume_graph', thread_id: threadId, resume_value: { start_time: startIso, end_time: endIso } })
    removeMsg(msgId)
    setTyping(true)
  }

  function onConfirmDvr(confirmed, vals, msgId) {
    const resume_value = { confirmed }
    if (confirmed) {
      resume_value.videoFormat = vals.videoFormat
      resume_value.videoResolution = vals.videoResolution
      resume_value.durationMinutes = vals.durationMinutes
      const details = {
        ...(currentConfirmParamsRef.current || {}),
        videoFormat: vals.videoFormat,
        videoResolution: vals.videoResolution,
        durationMinutes: vals.durationMinutes,
        clipStart: vals.clipStart ?? currentConfirmParamsRef.current?.clipStart ?? null,
        clipEnd: vals.clipEnd ?? null,
      }
      delete details.tripId
      lastDvrRequestDetailsRef.current = details
    }
    send({ type: 'resume_graph', thread_id: threadId, resume_value })
    removeMsg(msgId)
    if (confirmed) setTyping(true)
  }

  // ── new thread ────────────────────────────────────────────────────────
  function newThread() {
    commitCollected([])
    setGraphPaused(false)
    setCurrentTrips([])
    currentConfirmParamsRef.current = null
    lastDvrRequestDetailsRef.current = null
    setMessages([])
    setSummary('')
    setHighlight({ tripId: null, n: 0 })
    setThreadId('t_' + Date.now())
    setView('landing')
  }

  // ── derived ───────────────────────────────────────────────────────────
  const selectedTripId = useMemo(() => {
    const e = collectedItems.find((x) => x.option === 'Trips')
    return e ? e.selectedItem.tripId : null
  }, [collectedItems])

  const selectedTrip = useMemo(
    () => (selectedTripId != null ? currentTrips.find((t) => t.tripId === selectedTripId) : null),
    [selectedTripId, currentTrips],
  )

  const commonPill = {
    fleetData,
    currentTrips,
    collectedItems,
    onStage,
    onRemoveChip,
    onHighlightTripChip,
    sendWs: send,
    threadId,
  }

  const msgHandlers = { onStartDvr, onSubmitTimestamp, onConfirmDvr, onDismiss: removeMsg }

  return (
    <div className="shell">
      <TopBar connected={connected} onLoadFleet={loadFleet} onNewThread={newThread} />
      <div className="main">
        <div className="chat-panel">
          {/* Mutually exclusive: the landing screen and the chat screen can
              never be mounted at the same time. */}
          {view === 'landing' ? (
            <Landing key={threadId} pillProps={{ ...commonPill, onSend: landingSend }} />
          ) : (
            <>
              <MessagesArea active messages={messages} typing={typing} handlers={msgHandlers} />
              <ChatInputBar
                key={threadId}
                active
                selectedTrip={selectedTrip}
                onClearSelectedTrip={clearSelectedTrip}
                pillProps={{ ...commonPill, onSend: chatSend, seed: chatSeed }}
              />
            </>
          )}
        </div>
        <CanvasPanel
          trips={currentTrips}
          summary={summary}
          selectedTripId={selectedTripId}
          scrollTripId={highlight.tripId}
          scrollNonce={highlight.n}
          onUseTrip={onUseTrip}
        />
      </div>
    </div>
  )
}

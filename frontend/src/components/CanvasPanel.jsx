import { useEffect, useRef, useState } from 'react'
import { MIcon } from './common.jsx'
import { fmtUtcDateTime } from '../lib/format.js'

function TripRow({ trip, index, selected, scrollTarget, onUseTrip }) {
  const [evtOpen, setEvtOpen] = useState(false)
  const rowRef = useRef(null)

  useEffect(() => {
    if (scrollTarget && rowRef.current) rowRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [scrollTarget])

  const startFmt = trip.startTimeUTC ? fmtUtcDateTime(trip.startTimeUTC) : '—'
  const pingedFmt = trip.lastPinged ? fmtUtcDateTime(trip.lastPinged) : '—'
  const pingedColor = trip.lastPingedLabel === 'Ongoing' ? 'var(--green)' : 'var(--text-tri)'
  const statusClass =
    trip.dvr_status === 'available' ? 'badge-available' : trip.dvr_status === 'expiring' ? 'badge-expiring' : 'badge-expired'
  const statusLabel =
    trip.dvr_status === 'available' ? 'Available' : trip.dvr_status === 'expiring' ? 'Expiring' : 'Expired'
  const isExpired = trip.dvr_status === 'expired'
  const evtCount = trip.totalEvents || 0

  return (
    <div className={`tt-row${isExpired ? ' expired-row' : ''}${selected ? ' tt-row-selected' : ''}`} ref={rowRef}>
      <div className="tt-col col-asset">
        <div className="tt-asset">{trip.assetId}</div>
      </div>
      <div className="tt-col col-driver tt-cell">{trip.driverName}</div>
      <div className="tt-col col-start tt-cell">{startFmt}</div>
      <div className="tt-col col-pinged tt-cell">
        <span className="pinged-dot" style={{ background: pingedColor }}></span>
        {pingedFmt}
        <br />
        <span style={{ fontSize: 10, color: 'var(--text-tri)' }}>{trip.lastPingedLabel}</span>
      </div>
      <div className="tt-col col-events">
        {evtCount > 0 ? (
          <>
            <span className="evt-toggle" onClick={() => setEvtOpen((o) => !o)}>
              {evtCount} events {evtOpen ? '▾' : '▸'}
            </span>
            <div className={`evt-list${evtOpen ? ' open' : ''}`}>
              {(trip.events || []).map((e, i) => (
                <span className="evt-badge" key={i}>
                  {e.type} ({e.count})
                </span>
              ))}
            </div>
          </>
        ) : (
          <span style={{ fontSize: 11, color: 'var(--text-tri)' }}>None</span>
        )}
      </div>
      <div className="tt-col col-dvr">
        <span className={`badge ${statusClass}`}>{statusLabel}</span>
        {trip.dvr_until ? (
          <div style={{ fontSize: 10, color: 'var(--text-tri)', marginTop: 2 }}>{trip.dvr_until}</div>
        ) : null}
      </div>
      <div className="tt-col col-action">
        {isExpired ? (
          <button className="req-btn" disabled>
            No DVR
          </button>
        ) : (
          <button className={`req-btn${selected ? ' req-btn-selected' : ''}`} onClick={() => onUseTrip(index)}>
            {selected ? (
              <>
                <MIcon name="check" size={13} /> Selected
              </>
            ) : (
              'Use trip'
            )}
          </button>
        )}
      </div>
    </div>
  )
}

// Right-hand results canvas: header + trip table (or empty state).
export default function CanvasPanel({ trips, summary, selectedTripId, scrollTripId, scrollNonce, onUseTrip }) {
  const hasTrips = trips.length > 0
  return (
    <div className="canvas-panel">
      {hasTrips && (
        <div className="canvas-header">
          <span className="canvas-header-title">{summary}</span>
          <span className="canvas-count">
            {trips.length} trip{trips.length !== 1 ? 's' : ''}
          </span>
        </div>
      )}
      {!hasTrips && (
        <div className="canvas-empty">
          <div className="canvas-empty-icon">
            <MIcon name="videocam" size={40} />
          </div>
          <div>No trips found</div>
          <div style={{ fontSize: 11 }}>Search to see matching trips here</div>
        </div>
      )}
      {hasTrips && (
        <div className="trip-table">
          <div className="tt-head">
            <span className="tt-col col-asset">Asset / trip</span>
            <span className="tt-col col-driver">Driver</span>
            <span className="tt-col col-start">Start time</span>
            <span className="tt-col col-pinged">Last pinged</span>
            <span className="tt-col col-events">Safety events</span>
            <span className="tt-col col-dvr">DVR until</span>
            <span className="tt-col col-action">Action</span>
          </div>
          <div>
            {trips.map((t, i) => (
              <TripRow
                key={t.tripId || i}
                trip={t}
                index={i}
                selected={selectedTripId != null && t.tripId === selectedTripId}
                scrollTarget={scrollTripId != null && t.tripId === scrollTripId ? scrollNonce : null}
                onUseTrip={onUseTrip}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

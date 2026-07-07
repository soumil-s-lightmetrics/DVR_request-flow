import { MIcon } from './common.jsx'
import SearchPill from './SearchPill.jsx'
import { fmtUtcDateTime } from '../lib/format.js'

// Banner confirming which trip is staged for the DVR request (FIX 3).
function SelectedTripBanner({ trip, onClear }) {
  if (!trip) return null
  const startFmt = fmtUtcDateTime(trip.startTimeUTC)
  return (
    <div className="selected-trip-banner show">
      <span>
        <MIcon name="videocam" /> Selected for request: <strong>{trip.assetId || ''}</strong> · {trip.driverName || ''} ·{' '}
        {startFmt}
      </span>
      <button className="stb-clear" onClick={onClear}>
        Clear
      </button>
    </div>
  )
}

// Bottom chat composer: staged-trip banner + search pill + disclaimer.
export default function ChatInputBar({ active, selectedTrip, onClearSelectedTrip, pillProps }) {
  return (
    <div className={`chat-input-bar${active ? ' active' : ''}`}>
      <SelectedTripBanner trip={selectedTrip} onClear={onClearSelectedTrip} />
      <SearchPill variant="chat" {...pillProps} />
      <div className="chat-disclaimer">AI may make mistakes — verify trip details before submitting a video request</div>
    </div>
  )
}

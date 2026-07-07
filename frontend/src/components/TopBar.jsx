import { useState } from 'react'

// Top bar: fleet loader, connection status, new-chat button.
export default function TopBar({ connected, onLoadFleet, onNewThread }) {
  const [fleetId, setFleetId] = useState('')
  const [loadState, setLoadState] = useState('idle') // idle | loading | loaded | error

  const loadLabel =
    loadState === 'loading' ? 'Loading…' : loadState === 'loaded' ? 'Loaded' : loadState === 'error' ? 'Error' : 'Load fleet'

  async function handleLoad() {
    const fid = fleetId.trim()
    if (!fid) return
    setLoadState('loading')
    try {
      await onLoadFleet(fid)
      setLoadState('loaded')
      setTimeout(() => setLoadState('idle'), 2000)
    } catch (e) {
      console.error('Fleet load error:', e)
      setLoadState('error')
      setTimeout(() => setLoadState('idle'), 2000)
    }
  }

  return (
    <header className="topbar">
      <img src="/images/logo.jpg" alt="Logo" style={{ height: 32, borderRadius: 6 }} />
      <span className="topbar-title">Video Request</span>
      <span className="beta">BETA</span>
      <div className="topbar-right">
        <input
          className="fleet-input"
          placeholder="Fleet ID…"
          value={fleetId}
          onChange={(e) => setFleetId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleLoad()}
        />
        <button className="tb-btn tb-btn-p" onClick={handleLoad} disabled={loadState === 'loading'}>
          {loadLabel}
        </button>
        <div className="status-pill">
          <div className={`status-dot${connected ? ' live' : ''}`} />
          <span>{connected ? 'Connected' : 'Reconnecting…'}</span>
        </div>
        <button className="tb-btn tb-btn-g" onClick={onNewThread}>
          + New chat
        </button>
      </div>
    </header>
  )
}

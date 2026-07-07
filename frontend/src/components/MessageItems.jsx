import { useState } from 'react'
import { MIcon } from './common.jsx'
import { fmtClip, computeClipEnd } from '../lib/format.js'

// ── plain chat turns ────────────────────────────────────────────────────
export function UserMessage({ text }) {
  return (
    <div className="msg user">
      <div className="msg-avatar">U</div>
      <div className="msg-body">
        <div className="msg-role">You</div>
        <div className="msg-text">{text}</div>
      </div>
    </div>
  )
}

export function BotMessage({ text }) {
  return (
    <div className="msg assistant">
      <div className="msg-avatar">
        <MIcon name="smart_display" />
      </div>
      <div className="msg-body">
        <div className="msg-role">DVR assistant</div>
        <div className="gen-response">{text}</div>
      </div>
    </div>
  )
}

export function TypingIndicator() {
  return (
    <div className="msg assistant">
      <div className="msg-avatar">
        <MIcon name="smart_display" />
      </div>
      <div className="msg-body">
        <div className="msg-role">DVR assistant</div>
        <div className="typing">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
    </div>
  )
}

// ── clip / timelapse chips ──────────────────────────────────────────────
export function ActionChips({ large, onStartDvr }) {
  return (
    <div className={`action-chips${large ? ' action-chips-lg' : ''}`}>
      <button className="action-chip" onClick={() => onStartDvr('clip')}>
        <MIcon name="videocam" /> Request a DVR clip
      </button>
      <button className="action-chip" onClick={() => onStartDvr('timelapse')}>
        <MIcon name="timelapse" /> Request a timelapse
      </button>
    </div>
  )
}

// ── date-range interrupt ────────────────────────────────────────────────
export function TimestampInterrupt({ onSubmit, onCancel }) {
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  return (
    <div className="interrupt-wrap">
      <div className="interrupt-card">
        <div className="interrupt-card-head">
          <div className="ic-dot" style={{ background: 'var(--amber)' }}></div> Select date range
        </div>
        <div className="interrupt-card-body">
          <div className="ts-grid">
            <div>
              <div className="ts-label">From</div>
              <input className="ts-input" type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <div className="ts-label">To</div>
              <input className="ts-input" type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
          </div>
          <div className="btn-row">
            <button className="btn btn-purple" onClick={() => start && end && onSubmit(start, end)}>
              Search trips
            </button>
            <button className="btn btn-ghost" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── confirm-DVR interrupt ───────────────────────────────────────────────
const HIDDEN_KEYS = ['tripId', 'clipEnd']
const FRIENDLY_KEY = { driverId: 'Driver', assetId: 'Asset', type: 'Type' }

export function ConfirmDvrCard({ payload, onSubmit, onCancel }) {
  const { params, maxDurationMinutes: maxMin, videoFormatOptions, resolutionOptions } = payload
  const durationOptions = maxMin === 3 ? [0.5, 1, 2, 3] : [15, 30, 45, 60]
  const clipStartRaw = params.clipStart

  const [format, setFormat] = useState(videoFormatOptions[0]?.value)
  const [resolution, setResolution] = useState(resolutionOptions[0])
  const [duration, setDuration] = useState(durationOptions[0])

  const clipStart = fmtClip(clipStartRaw)
  const clipEnd = computeClipEnd(clipStartRaw, parseFloat(duration))

  const rows = Object.entries(params)
    .filter(([k]) => !HIDDEN_KEYS.includes(k))
    .map(([k, v]) => (
      <div className="dvr-param-row" key={k}>
        <span className="dvr-param-key">{FRIENDLY_KEY[k] || k}</span>
        <span className="dvr-param-val">{String(v)}</span>
      </div>
    ))

  function submit() {
    onSubmit({
      videoFormat: format,
      videoResolution: resolution,
      durationMinutes: parseFloat(duration),
      clipStart,
      clipEnd,
    })
  }

  return (
    <div className="interrupt-wrap">
      <div className="interrupt-card">
        <div className="interrupt-card-head">
          <div className="ic-dot" style={{ background: 'var(--purple)' }}></div> Confirm DVR request
        </div>
        <div className="interrupt-card-body">
          <div className="dvr-params">{rows}</div>
          <div className="clip-window">
            <div className="clip-box">
              <div className="ts-label">Clip start</div>
              <div className="clip-val">{clipStart}</div>
            </div>
            <div className="clip-box">
              <div className="ts-label">Clip end (start + duration)</div>
              <div className="clip-val">{clipEnd}</div>
            </div>
          </div>
          <div className="ts-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr', marginTop: 10 }}>
            <div>
              <div className="ts-label">Video format</div>
              <select className="ts-input" value={format} onChange={(e) => setFormat(e.target.value)}>
                {videoFormatOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <div className="ts-label">Duration (max {maxMin}m)</div>
              <select className="ts-input" value={duration} onChange={(e) => setDuration(e.target.value)}>
                {durationOptions.map((d) => (
                  <option key={d} value={d}>
                    {d} min
                  </option>
                ))}
              </select>
            </div>
            <div>
              <div className="ts-label">Resolution</div>
              <select className="ts-input" value={resolution} onChange={(e) => setResolution(e.target.value)}>
                {resolutionOptions.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="btn-row" style={{ marginTop: 14 }}>
            <button className="btn btn-purple" onClick={submit}>
              Submit request
            </button>
            <button className="btn btn-ghost" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── success banner ──────────────────────────────────────────────────────
const LABEL_MAP = {
  driverId: 'Driver',
  assetId: 'Asset',
  type: 'Type',
  clipStart: 'Clip start',
  clipEnd: 'Clip end',
  videoFormat: 'Format',
  videoResolution: 'Resolution',
  durationMinutes: 'Duration (min)',
}
const DETAIL_ORDER = ['driverId', 'assetId', 'type', 'clipStart', 'clipEnd', 'videoFormat', 'videoResolution', 'durationMinutes']

export function SuccessBanner({ id, details, summary }) {
  let detailBlock = null
  if (details) {
    detailBlock = (
      <div className="dvr-params">
        {DETAIL_ORDER.filter((k) => details[k] !== undefined && details[k] !== null).map((k) => (
          <div className="dvr-param-row" key={k}>
            <span className="dvr-param-key">{LABEL_MAP[k] || k}</span>
            <span className="dvr-param-val">{String(details[k])}</span>
          </div>
        ))}
      </div>
    )
  } else if (summary) {
    detailBlock = (
      <div style={{ fontSize: 11, color: 'var(--text-tri)', marginTop: 6 }}>
        {summary.type} · {summary.videoFormat} · {summary.videoResolution}
      </div>
    )
  }
  return (
    <div className="result-banner">
      <div className="result-banner-label">
        <MIcon name="check_circle" size={13} /> DVR request raised successfully
      </div>
      <div className="result-banner-id">{id}</div>
      {detailBlock}
    </div>
  )
}

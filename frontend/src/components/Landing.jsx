import { useState } from 'react'
import { MIcon } from './common.jsx'
import SearchPill from './SearchPill.jsx'

const QUICK_CARDS = [
  { icon: 'person', title: 'Driver lookup', sub: 'Last trip, incident history', q: 'Show last trip for driver ' },
  { icon: 'videocam', title: 'Trip footage', sub: 'Clips, timelapse, departure', q: 'Get DVR footage for trip ' },
  { icon: 'warning', title: 'Event review', sub: 'Violations, risk events', q: 'Show all harsh brake events ' },
  { icon: 'local_shipping', title: 'Asset footage', sub: 'Full trip video by vehicle', q: 'Get whole trip video for asset ' },
]

// Landing screen: prompt, search pill, and quick-start cards.
export default function Landing({ pillProps }) {
  const [seed, setSeed] = useState({ text: '', n: 0 })

  return (
    <div className="landing">
      <div className="landing-title">What footage do you need?</div>
      <SearchPill variant="landing" seed={seed} {...pillProps} />
      <div className="quick-cards">
        {QUICK_CARDS.map((c) => (
          <div className="quick-card" key={c.title} onClick={() => setSeed((s) => ({ text: c.q, n: s.n + 1 }))}>
            <div className="qc-icon">
              <MIcon name={c.icon} size={14} />
            </div>
            <div>
              <div className="qc-title">{c.title}</div>
              <div className="qc-sub">{c.sub}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

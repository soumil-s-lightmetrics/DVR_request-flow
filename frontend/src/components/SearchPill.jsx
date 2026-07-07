import { useEffect, useMemo, useRef, useState } from 'react'
import { MIcon, Highlight, SendIcon } from './common.jsx'
import { CATEGORY_ICON, friendlyTripLabel } from '../lib/format.js'
import { globalSearch, localSearch, stageEntry } from '../lib/search.js'

const CATS = [
  { m: 'Drivers', i: 'person', l: 'Drivers' },
  { m: 'Assets', i: 'local_shipping', l: 'Assets' },
  { m: 'Trips', i: 'route', l: 'Trips' },
  { m: 'Event Types', i: 'warning', l: 'Event types' },
]
const GROUP_ORDER = ['Drivers', 'Assets', 'Trips', 'Event Types']

// Label shown on a chip for a collectedItems entry (ported from rerenderChips).
function chipLabel(e) {
  if (e.option === 'Drivers') return e.selectedItem.driverName || e.selectedItem.driverId
  if (e.option === 'DateRange') return e.selectedItem.label
  if (e.option === 'Trips') return e.selectedItem.label || friendlyTripLabel({ driverName: 'Trip', assetId: '' })
  return e.selectedItem.assetId || e.selectedItem.event_type || ''
}

// Shared search input with @-mention autocomplete + selected-filter chips.
// variant: 'landing' (text input, dropdown below) | 'chat' (textarea, dropdown above)
export default function SearchPill({
  variant,
  fleetData,
  currentTrips,
  collectedItems,
  onStage,
  onRemoveChip,
  onHighlightTripChip,
  onSend,
  sendWs,
  threadId,
  seed,
}) {
  const [value, setValue] = useState('')
  const [acOpen, setAcOpen] = useState(false)
  const [selectedCategory, setSelectedCategory] = useState(null)
  const [ac, setAc] = useState({ mode: 'cats' })
  const [focusIdx, setFocusIdx] = useState(-1)

  const valueRef = useRef('')
  const textBeforeAtRef = useRef('')
  const inputRef = useRef(null)

  const setVal = (v) => {
    valueRef.current = v
    setValue(v)
  }

  // Seed the input text from an external trigger (landing quick cards -> setQ).
  useEffect(() => {
    if (seed && seed.n) {
      setVal(seed.text)
      focusInput()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed?.n])

  // Auto-resize the chat textarea to fit its content.
  useEffect(() => {
    if (variant === 'chat' && inputRef.current) {
      const el = inputRef.current
      el.style.height = 'auto'
      el.style.height = el.scrollHeight + 'px'
    }
  }, [value, variant])

  // ── autocomplete search state builders ────────────────────────────────
  const runGlobal = (q) => {
    setAc({ mode: 'global', query: q, ...globalSearch(fleetData, currentTrips, q) })
    setFocusIdx(-1)
  }
  const runLocal = (cat, q) => {
    setAc({ mode: 'local', cat, query: q, items: localSearch(fleetData, currentTrips, cat, q) })
    setFocusIdx(-1)
  }
  const showCats = () => {
    setAc({ mode: 'cats' })
    setFocusIdx(-1)
  }
  const closeAc = () => {
    setAcOpen(false)
    setFocusIdx(-1)
  }
  const focusInput = () => inputRef.current?.focus()

  // Flat list of selectable rows for keyboard navigation.
  const selectable = useMemo(() => {
    if (ac.mode === 'global') return GROUP_ORDER.flatMap((cat) => ac.groups[cat].map((match) => ({ cat, match })))
    if (ac.mode === 'local') return ac.items.map((match) => ({ cat: ac.cat, match }))
    return []
  }, [ac])

  // Scroll the focused row into view.
  useEffect(() => {
    if (focusIdx < 0 || !inputRef.current) return
    const root = inputRef.current.closest('.search-pill-wrap, .chat-pill-wrap')
    root?.querySelectorAll('.chat-ac-item')[focusIdx]?.scrollIntoView({ block: 'nearest' })
  }, [focusIdx])

  // ── category scope ─────────────────────────────────────────────────────
  function injectCat(cat) {
    const cur = valueRef.current
    const ai = cur.lastIndexOf('@')
    textBeforeAtRef.current = ai !== -1 ? cur.slice(0, ai) : cur
    setSelectedCategory(cat)
    setVal('@')
    sendWs({ type: 'autocomplete', option: cat, search: '', thread_id: threadId })
    runLocal(cat, '')
    setAcOpen(true)
    focusInput()
  }

  function clearCatScope() {
    setSelectedCategory(null)
    setVal(textBeforeAtRef.current + '@')
    runGlobal('')
    setAcOpen(true)
    focusInput()
  }

  // Stage a picked match: add the chip AND write the label back into the text
  // (FIX 2 — so the backend's merge_filters_from_text sees what was picked).
  function stageMatch(cat, match) {
    onStage(stageEntry(cat, match))
    setVal(textBeforeAtRef.current + (cat !== 'Trips' ? match.label + ' ' : ''))
    textBeforeAtRef.current = ''
    closeAc()
    setSelectedCategory(null)
    focusInput()
  }

  // ── input handler (ported from evaluateAutocompleteTrigger) ────────────
  function handleInput(newValue) {
    if (selectedCategory) {
      setVal(newValue)
      const ai = newValue.lastIndexOf('@')
      const q = ai !== -1 ? newValue.slice(ai + 1) : newValue
      runLocal(selectedCategory, q.trim())
      setAcOpen(true)
      return
    }
    const ai = newValue.lastIndexOf('@')
    if (ai === -1) {
      setVal(newValue)
      closeAc()
      return
    }
    textBeforeAtRef.current = newValue.slice(0, ai)
    const after = newValue.slice(ai + 1)
    if (!after) {
      setVal(newValue)
      showCats()
      setAcOpen(true)
      return
    }
    const ns = after.toLowerCase()
    let ec = null
    if (ns.startsWith('drivers ')) ec = 'Drivers'
    else if (ns.startsWith('assets ')) ec = 'Assets'
    else if (ns.startsWith('trips ')) ec = 'Trips'
    else if (ns.startsWith('event types ') || ns.startsWith('events ')) ec = 'Event Types'
    if (ec) {
      injectCat(ec)
    } else {
      setVal(newValue)
      runGlobal(after.trim())
      setAcOpen(true)
    }
  }

  function doSend() {
    const sent = onSend(valueRef.current)
    if (sent !== false) {
      setVal('')
      closeAc()
    }
  }

  // ── keyboard (ported from handleKD) ────────────────────────────────────
  function handleKeyDown(e) {
    if (!acOpen) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        doSend()
      }
      return
    }
    if (selectable.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setFocusIdx((i) => Math.min(i + 1, selectable.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setFocusIdx((i) => Math.max(i - 1, 0))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        if (focusIdx >= 0) {
          const { cat, match } = selectable[focusIdx]
          stageMatch(cat, match)
        } else doSend()
      } else if (e.key === 'Escape') {
        closeAc()
      }
    } else if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      doSend()
    }
  }

  // ── results panel ──────────────────────────────────────────────────────
  function renderResults() {
    if (ac.mode === 'cats') {
      return (
        <>
          <div className="filter-panel-head">Select category</div>
          <div className="filter-item-scroll">
            {CATS.map((x) => (
              <div className="filter-row" key={x.m} onClick={() => injectCat(x.m)}>
                <div className="filter-icon">
                  <MIcon name={x.i} />
                </div>
                <div className="filter-info">
                  <div className="filter-name">{x.l}</div>
                </div>
                <div className="filter-chevron">›</div>
              </div>
            ))}
          </div>
        </>
      )
    }

    if (ac.mode === 'local') {
      const icon = CATEGORY_ICON[ac.cat] || 'category'
      return (
        <>
          <div className="filter-panel-head">
            <button className="back-btn" onClick={clearCatScope}>
              ← Back
            </button>
            <span>{ac.cat}</span>
          </div>
          {ac.items.length === 0 ? (
            <div className="no-data">No matches</div>
          ) : (
            <div className="filter-item-scroll">
              {ac.items.map((x, i) => (
                <div
                  className={`filter-row chat-ac-item${i === focusIdx ? ' focused' : ''}`}
                  key={i}
                  onClick={() => stageMatch(ac.cat, x)}
                >
                  <div className="filter-icon">
                    <MIcon name={icon} />
                  </div>
                  <div className="filter-info">
                    <div className="filter-name">
                      <Highlight text={x.label} query={ac.query} />
                    </div>
                    {x.sub ? (
                      <div className="filter-sub">
                        <Highlight text={x.sub} query={ac.query} />
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )
    }

    // global
    if (ac.count === 0) return <div className="no-data">No matches</div>
    let gid = -1
    return (
      <>
        <div className="filter-panel-head">Results for "{ac.query}"</div>
        <div className="filter-item-scroll">
          {GROUP_ORDER.map((cat) => {
            const col = ac.groups[cat]
            if (!col.length) return null
            return (
              <div key={cat}>
                <div className="ac-group-header" onClick={() => injectCat(cat)}>
                  <span>{cat}</span>
                  <span className="group-action-hint">click to filter</span>
                </div>
                {col.map((item) => {
                  gid += 1
                  const idx = gid
                  return (
                    <div
                      className={`filter-row chat-ac-item${idx === focusIdx ? ' focused' : ''}`}
                      key={idx}
                      onClick={() => stageMatch(cat, item)}
                    >
                      <div className="filter-icon">
                        <MIcon name={CATEGORY_ICON[cat]} />
                      </div>
                      <div className="filter-info">
                        <div className="filter-name">
                          <Highlight text={item.label} query={ac.query} />
                        </div>
                        {item.sub ? (
                          <div className="filter-sub">
                            <Highlight text={item.sub} query={ac.query} />
                          </div>
                        ) : null}
                      </div>
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
      </>
    )
  }

  // ── chips ──────────────────────────────────────────────────────────────
  const chips = collectedItems.map((e, i) => {
    const label = String(chipLabel(e))
    return (
      <span className="selected-tag" key={i}>
        {e.option === 'Trips' ? (
          <span onClick={() => onHighlightTripChip(i)} style={{ cursor: 'pointer' }}>
            <MIcon name={CATEGORY_ICON[e.option] || 'category'} size={13} /> {label}
          </span>
        ) : (
          <>
            <MIcon name={CATEGORY_ICON[e.option] || 'category'} size={13} /> {label}
          </>
        )}{' '}
        <button className="selected-tag-remove" onClick={() => onRemoveChip(i)}>
          ×
        </button>
      </span>
    )
  })

  // ── markup ───────────────────────────────────────────────────────────────
  if (variant === 'landing') {
    return (
      <div className="search-pill-wrap">
        <div className="search-pill">
          {chips.length > 0 && <div className="pill-tags">{chips}</div>}
          <div className="pill-input-row">
            <input
              id="main-input"
              ref={inputRef}
              type="text"
              placeholder="Ask anything or type @ to filter…"
              value={value}
              onChange={(e) => handleInput(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <button className="send-btn" onClick={doSend}>
              <SendIcon />
            </button>
          </div>
        </div>
        <div className={`filter-panel${acOpen ? ' open' : ''}`}>{renderResults()}</div>
      </div>
    )
  }

  return (
    <div className="chat-pill-wrap">
      <div className={`chat-ac-dropdown${acOpen ? ' open' : ''}`}>{renderResults()}</div>
      <div className="chat-pill">
        {chips.length > 0 && <div className="pill-tags">{chips}</div>}
        <div className="pill-input-row">
          <textarea
            id="chat-input"
            ref={inputRef}
            rows={1}
            placeholder="Ask anything or type @ to filter…"
            value={value}
            onChange={(e) => handleInput(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button className="chat-send-btn" onClick={doSend}>
            <SendIcon />
          </button>
        </div>
      </div>
    </div>
  )
}

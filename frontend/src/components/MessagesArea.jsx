import { useEffect, useRef } from 'react'
import {
  UserMessage,
  BotMessage,
  TypingIndicator,
  ActionChips,
  TimestampInterrupt,
  ConfirmDvrCard,
  SuccessBanner,
} from './MessageItems.jsx'

// Ordered message log. Renders each item by kind and keeps the view scrolled
// to the bottom as content arrives.
export default function MessagesArea({ active, messages, typing, handlers }) {
  const ref = useRef(null)

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [messages, typing])

  return (
    <div className={`messages-area${active ? ' active' : ''}`} ref={ref}>
      {messages.map((m) => {
        switch (m.kind) {
          case 'user':
            return <UserMessage key={m.id} text={m.text} />
          case 'bot':
            return <BotMessage key={m.id} text={m.text} />
          case 'action-chips':
            return <ActionChips key={m.id} onStartDvr={handlers.onStartDvr} />
          case 'trip-type-prompt':
            return <ActionChips key={m.id} large onStartDvr={handlers.onStartDvr} />
          case 'ts-interrupt':
            return (
              <TimestampInterrupt
                key={m.id}
                onSubmit={(s, e) => handlers.onSubmitTimestamp(s, e, m.id)}
                onCancel={() => handlers.onDismiss(m.id)}
              />
            )
          case 'confirm-dvr':
            return (
              <ConfirmDvrCard
                key={m.id}
                payload={m.payload}
                onSubmit={(vals) => handlers.onConfirmDvr(true, vals, m.id)}
                onCancel={() => handlers.onConfirmDvr(false, null, m.id)}
              />
            )
          case 'success':
            return <SuccessBanner key={m.id} id={m.id2} details={m.details} summary={m.summary} />
          default:
            return null
        }
      })}
      {typing && <TypingIndicator />}
    </div>
  )
}

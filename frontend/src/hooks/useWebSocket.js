import { useEffect, useRef, useState, useCallback } from 'react'

// Plain WebSocket connection with the original's queue + exponential-backoff
// reconnect. onMessage is kept in a ref so the socket callbacks always see the
// latest handler without needing to reconnect.
export function useWebSocket(onMessage) {
  const wsRef = useRef(null)
  const readyRef = useRef(false)
  const queueRef = useRef([])
  const delayRef = useRef(1000)
  const handlerRef = useRef(onMessage)
  const [connected, setConnected] = useState(false)

  handlerRef.current = onMessage

  const getWsUrl = () => {
    // In dev, VITE_WS_URL (see frontend/.env.development) points straight at the
    // Flask backend, bypassing Vite's WebSocket proxy — which otherwise spams
    // harmless `write EPIPE` errors on every reload. In prod the var is unset,
    // so we fall back to a same-origin URL (Flask serves the built app).
    if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${location.host}/chat`
  }

  const connect = useCallback(() => {
    const ws = new WebSocket(getWsUrl())
    wsRef.current = ws

    ws.onopen = () => {
      readyRef.current = true
      delayRef.current = 1000
      setConnected(true)
      queueRef.current.forEach((msg) => ws.send(JSON.stringify(msg)))
      queueRef.current = []
    }

    ws.onclose = () => {
      readyRef.current = false
      setConnected(false)
      setTimeout(connect, delayRef.current)
      delayRef.current = Math.min(delayRef.current * 2, 10000)
    }

    ws.onerror = (err) => {
      console.error('WebSocket error:', err)
      ws.close()
    }

    ws.onmessage = (event) => {
      try {
        handlerRef.current?.(JSON.parse(event.data))
      } catch (e) {
        console.error('Failed to parse server message:', e)
      }
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      const ws = wsRef.current
      if (ws) {
        ws.onclose = null // don't trigger auto-reconnect on unmount
        ws.close()
      }
    }
  }, [connect])

  // Send helper — queues if the socket is not open yet.
  const send = useCallback((payload) => {
    if (wsRef.current && readyRef.current) {
      wsRef.current.send(JSON.stringify(payload))
    } else {
      queueRef.current.push(payload)
    }
  }, [])

  return { send, connected }
}

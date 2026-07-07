# DVR Fleet Assistant — React frontend

React (Vite) port of the original single-file `DVR_frontend.html`. Same UI,
same WebSocket protocol, same styling — restructured into components.

## Develop

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

The dev server proxies the backend API (see `vite.config.js`):

- `ws://…/chat` — agent WebSocket stream
- `/<fleet_id>/load-data`, `/health` — REST
- `/images`, `/static` — assets

Point it at a non-default backend with `BACKEND_URL=http://host:port npm run dev`.
The Flask backend (`main-DVR.py`) should be running on that host (default
`http://localhost:8000`).

## Build

```bash
npm run build      # outputs to frontend/dist
npm run preview
```

Serve `dist/` behind the same origin as the backend (or keep the reverse
proxy), since the app connects to `${location.host}/chat` and calls the REST
routes with same-origin paths — identical to the original page.

## Structure

```
src/
  main.jsx                 # React entry
  App.jsx                  # top-level state + WebSocket message routing
  styles.css               # ported verbatim from the original <style> block
  hooks/useWebSocket.js     # plain WS w/ queue + backoff reconnect
  lib/format.js            # date / clip formatting, category icons
  lib/search.js            # @-autocomplete search over fleet data
  components/
    TopBar.jsx             # fleet loader, status, new chat
    Landing.jsx            # landing prompt + quick cards
    SearchPill.jsx         # shared @-mention input + chips (landing & chat)
    MessagesArea.jsx       # ordered message log
    MessageItems.jsx       # message / interrupt / confirm / success cards
    ChatInputBar.jsx       # composer + staged-trip banner
    CanvasPanel.jsx        # trip results table
    common.jsx             # MIcon, Highlight, SendIcon
```

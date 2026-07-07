import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './styles.css'

// StrictMode is intentionally omitted: in dev it double-mounts components,
// which would open → close → reopen the persistent /chat WebSocket on every
// load and spam Vite's proxy with harmless `write EPIPE` errors.
ReactDOM.createRoot(document.getElementById('root')).render(<App />)

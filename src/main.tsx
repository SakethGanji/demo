import './lib/uuid-polyfill'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// nteract.css first: modern-tokens.css re-points its ANSI colours at the --ne-* tokens
import './nteract.css'
import './tokens/tokens.css'
import './tokens/modern-tokens.css'
import './app.css'
import { App } from './App'

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)

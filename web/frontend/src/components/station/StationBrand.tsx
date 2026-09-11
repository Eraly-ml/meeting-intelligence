import { Moon, Sun } from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'
import './station-shell.css'

export function StationBrand({ compact = false }: { compact?: boolean }) {
  return <span className="ms-brand">
    <svg className="ms-brand-mark" viewBox="0 0 40 40" fill="none" aria-hidden="true">
      <rect width="40" height="40" rx="4" fill="currentColor" />
      <path d="M11 18v5m6-11v17m6-14v11m6-7v3" stroke="var(--ms-mark-ink, #fff)" strokeWidth="3" strokeLinecap="round" />
    </svg>
    {!compact && <span className="ms-brand-name">Meeting<span>Station</span></span>}
  </span>
}

export function StationThemeButton() {
  const { theme, toggleTheme } = useTheme()
  return <button className="ms-icon-button" onClick={toggleTheme} type="button" aria-label="Toggle theme" title="Toggle theme">
    {theme === 'light' ? <Moon /> : <Sun />}
  </button>
}

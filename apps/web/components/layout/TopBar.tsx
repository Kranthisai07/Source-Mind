'use client'

import { type ReactNode } from 'react'

interface TopBarProps {
  title: string
  subtitle?: string
  actions?: ReactNode
}

export function TopBar({ title, subtitle, actions }: TopBarProps) {
  return (
    <div className="flex items-center justify-between px-8 py-5 border-b border-border bg-surface/50 backdrop-blur-sm sticky top-0 z-10">
      <div>
        <h2 className="text-lg font-semibold text-primary leading-tight">{title}</h2>
        {subtitle && <p className="text-secondary text-xs mt-0.5 font-mono">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-3">{actions}</div>}
    </div>
  )
}

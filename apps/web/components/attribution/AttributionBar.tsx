'use client'

import { useState } from 'react'
import { type Contributor } from '@/lib/types'
import { formatPct, hashColor } from '@/lib/utils'

interface AttributionBarProps {
  contributors: Contributor[]
  height?: number
  showLabels?: boolean
}

export function AttributionBar({ contributors, height = 8, showLabels = false }: AttributionBarProps) {
  const [hovered, setHovered] = useState<string | null>(null)

  if (!contributors.length) return null

  return (
    <div>
      {/* Stacked bar */}
      <div
        className="flex overflow-hidden rounded-full relative"
        style={{ height }}
      >
        {contributors.map((c) => {
          const color = hashColor(c.name)
          const width = Math.max(c.contribution_pct * 100, 1)
          const isHovered = hovered === c.id

          return (
            <div
              key={c.id}
              className="relative group transition-all duration-150"
              style={{ width: `${width}%`, backgroundColor: color, opacity: isHovered ? 1 : 0.8 }}
              onMouseEnter={() => setHovered(c.id)}
              onMouseLeave={() => setHovered(null)}
            />
          )
        })}
      </div>

      {/* Tooltip */}
      {hovered && (
        <div className="pointer-events-none">
          {contributors.filter(c => c.id === hovered).map(c => (
            <div key={c.id} className="text-xs text-secondary mt-1 font-mono">
              {c.name}: {formatPct(c.contribution_pct)}
            </div>
          ))}
        </div>
      )}

      {/* Labels */}
      {showLabels && (
        <div className="flex flex-wrap gap-2 mt-3">
          {contributors.map((c) => (
            <div key={c.id} className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: hashColor(c.name) }} />
              <span className="text-xs text-secondary">{c.name}</span>
              <span className="text-xs text-muted font-mono">{formatPct(c.contribution_pct)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

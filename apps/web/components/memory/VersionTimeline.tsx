'use client'

import { type MemoryVersion } from '@/lib/types'
import { formatDate, cn } from '@/lib/utils'
import { CheckCircle } from 'lucide-react'

interface VersionTimelineProps {
  versions: MemoryVersion[]
  currentId: string
  onSelect: (version: MemoryVersion) => void
}

export function VersionTimeline({ versions, currentId, onSelect }: VersionTimelineProps) {
  if (!versions.length) return null

  return (
    <div className="relative">
      <h4 className="text-xs text-secondary font-mono uppercase tracking-wider mb-4">Version History</h4>
      <div className="flex items-center gap-0 overflow-x-auto no-scrollbar pb-2">
        {versions.map((v, i) => {
          const isCurrent = v.id === currentId || v.is_current
          const isSelected = v.id === currentId

          return (
            <div key={v.id} className="flex items-center flex-shrink-0">
              {/* Node */}
              <button
                onClick={() => onSelect(v)}
                className="flex flex-col items-center group"
              >
                <div className="mb-1">
                  <span className="text-xs text-muted font-mono">{formatDate(v.created_at)}</span>
                </div>
                <div
                  className={cn(
                    'flex items-center justify-center rounded-full border-2 transition-all duration-150',
                    isCurrent
                      ? 'w-9 h-9 border-accent-blue bg-[rgba(79,142,247,0.15)]'
                      : isSelected
                      ? 'w-7 h-7 border-accent-blue'
                      : 'w-6 h-6 border-border bg-surface group-hover:border-border-active'
                  )}
                >
                  {isCurrent ? (
                    <CheckCircle className="w-4 h-4 text-accent-blue" />
                  ) : (
                    <span className="text-xs font-mono text-muted">v{v.version}</span>
                  )}
                </div>
                {isCurrent && (
                  <span className="text-[10px] text-accent-blue font-mono mt-1">CURRENT</span>
                )}
              </button>

              {/* Connector */}
              {i < versions.length - 1 && (
                <div className="w-12 h-px bg-border mx-1 flex-shrink-0" />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

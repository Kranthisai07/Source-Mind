'use client'

import { AlertTriangle, Clock, Users, Zap } from 'lucide-react'
import { riskColor, riskBg } from '@/lib/utils'
import type { KnowledgeGap } from '@/lib/types'

interface KnowledgeGapsProps {
  gaps: KnowledgeGap[]
}

const GAP_ICONS = {
  single_contributor: Users,
  no_recent_update: Clock,
  high_conflict_area: Zap,
}

export function KnowledgeGaps({ gaps }: KnowledgeGapsProps) {
  if (!gaps.length) {
    return (
      <div className="flex items-center gap-3 py-6 text-muted text-sm">
        <AlertTriangle className="w-4 h-4" />
        No knowledge gaps detected. Great coverage!
      </div>
    )
  }

  const sorted = [...gaps].sort((a, b) => {
    const order = { HIGH: 0, MEDIUM: 1, LOW: 2 }
    return order[a.risk_level] - order[b.risk_level]
  })

  return (
    <div className="space-y-3">
      {sorted.map((gap, i) => {
        const Icon = GAP_ICONS[gap.gap_type] ?? AlertTriangle
        const color = riskColor(gap.risk_level)
        const bg = riskBg(gap.risk_level)

        return (
          <div
            key={i}
            className="flex items-start gap-4 p-4 rounded-xl border transition-colors"
            style={{ backgroundColor: bg, borderColor: `${color}30` }}
          >
            <div
              className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center mt-0.5"
              style={{ backgroundColor: `${color}20` }}
            >
              <Icon className="w-4 h-4" style={{ color }} />
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-mono font-medium" style={{ color }}>
                  {gap.risk_level}
                </span>
                <span className="text-xs text-muted font-mono">·</span>
                <span className="text-xs text-muted font-mono">
                  {gap.affected_memories} {gap.affected_memories === 1 ? 'memory' : 'memories'} affected
                </span>
              </div>
              <p className="text-sm text-primary mb-1">{gap.description}</p>
              <p className="text-xs text-secondary">{gap.recommendation}</p>
            </div>
          </div>
        )
      })}
    </div>
  )
}

'use client'

import { ContributorAvatar } from '@/components/ui/ContributorAvatar'
import { formatDate, formatPct, hashColor } from '@/lib/utils'
import type { ContributorMapEntry } from '@/lib/types'

interface ContributorMapProps {
  contributors: ContributorMapEntry[]
}

export function ContributorMap({ contributors }: ContributorMapProps) {
  if (!contributors.length) {
    return <p className="text-muted text-sm">No contributor data available.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            {['Contributor', 'Created', 'Influenced', 'Avg Contribution', 'Collaboration', 'Domains', 'Last Active'].map((h) => (
              <th key={h} className="text-left text-xs text-muted font-mono uppercase tracking-wider pb-3 pr-4 whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {contributors.map((c) => (
            <tr key={c.user_id} className="hover:bg-surface/50 transition-colors">
              <td className="py-3 pr-4">
                <div className="flex items-center gap-3">
                  <ContributorAvatar name={c.name} size="sm" />
                  <span className="text-primary font-medium">{c.name}</span>
                </div>
              </td>
              <td className="py-3 pr-4">
                <span className="text-secondary font-mono">{c.total_memories_created}</span>
              </td>
              <td className="py-3 pr-4">
                <span className="text-secondary font-mono">{c.total_memories_influenced}</span>
              </td>
              <td className="py-3 pr-4">
                <div className="flex items-center gap-2">
                  <div className="w-16 h-1 bg-border rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${c.avg_contribution_pct * 100}%`,
                        backgroundColor: hashColor(c.name),
                      }}
                    />
                  </div>
                  <span className="text-secondary font-mono text-xs">{formatPct(c.avg_contribution_pct)}</span>
                </div>
              </td>
              <td className="py-3 pr-4">
                <CollaborationBadge rate={c.collaboration_rate} />
              </td>
              <td className="py-3 pr-4">
                <div className="flex flex-wrap gap-1">
                  {(c.knowledge_domains ?? []).slice(0, 3).map((d) => (
                    <span key={d} className="text-[10px] font-mono bg-elevated border border-border rounded px-1.5 py-0.5 text-muted">
                      {d}
                    </span>
                  ))}
                  {(c.knowledge_domains ?? []).length > 3 && (
                    <span className="text-[10px] font-mono text-muted">+{c.knowledge_domains.length - 3}</span>
                  )}
                </div>
              </td>
              <td className="py-3 pr-4">
                <span className="text-muted text-xs font-mono">{formatDate(c.last_contribution_at)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CollaborationBadge({ rate }: { rate: number }) {
  const pct = rate * 100
  const color = pct >= 70 ? '#10B981' : pct >= 40 ? '#F59E0B' : '#EF4444'
  return (
    <span className="text-xs font-mono" style={{ color }}>
      {pct.toFixed(0)}%
    </span>
  )
}

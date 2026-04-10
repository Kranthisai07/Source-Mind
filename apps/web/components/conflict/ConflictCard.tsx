import Link from 'next/link'
import { type Conflict } from '@/lib/types'
import { ConflictStatusBadge } from '@/components/ui/Badge'
import { truncate, formatRelativeTime, cn } from '@/lib/utils'
import { ArrowRight } from 'lucide-react'

interface ConflictCardProps {
  conflict: Conflict
}

function ScoreBar({ score }: { score: number }) {
  const color = score >= 0.8 ? '#EF4444' : score >= 0.5 ? '#F59E0B' : '#8888A0'
  return (
    <div className="flex items-center gap-3">
      <span className="text-2xl font-display font-medium" style={{ color }}>
        {score.toFixed(2)}
      </span>
      <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${score * 100}%`, backgroundColor: color }} />
      </div>
    </div>
  )
}

export function ConflictCard({ conflict }: ConflictCardProps) {
  const score = conflict.conflict_score ?? conflict.similarity_score ?? 0
  const memA = conflict.memory_a?.content ?? conflict.memory_a_content ?? ''
  const memB = conflict.memory_b?.content ?? conflict.memory_b_content ?? ''

  return (
    <Link href={`/conflicts/${conflict.id}`}>
      <div className="group bg-surface border border-border rounded-xl p-5 card-hover">
        <div className="flex items-start justify-between gap-4 mb-4">
          <ScoreBar score={score} />
          <div className="flex items-center gap-2 flex-shrink-0">
            {conflict.severity && (
              <span className="text-xs font-mono text-muted uppercase">{conflict.conflict_type}</span>
            )}
            <ConflictStatusBadge status={conflict.status} />
          </div>
        </div>

        {/* Side-by-side snippet */}
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-bg rounded-lg p-3 border border-border">
            <p className="text-xs text-muted font-mono mb-1">Memory A</p>
            <p className="text-sm text-secondary line-clamp-2">{truncate(memA, 120)}</p>
          </div>
          <div className="bg-bg rounded-lg p-3 border border-border">
            <p className="text-xs text-muted font-mono mb-1">Memory B</p>
            <p className="text-sm text-secondary line-clamp-2">{truncate(memB, 120)}</p>
          </div>
        </div>

        <div className="flex items-center justify-between mt-4">
          <span className="text-xs text-muted font-mono">{formatRelativeTime(conflict.created_at)}</span>
          <div className="flex items-center gap-1 text-accent-blue text-xs opacity-0 group-hover:opacity-100 transition-opacity">
            Review <ArrowRight className="w-3 h-3" />
          </div>
        </div>
      </div>
    </Link>
  )
}

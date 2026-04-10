'use client'

import Link from 'next/link'
import { type Memory } from '@/lib/types'
import { truncate, formatRelativeTime } from '@/lib/utils'
import { AttributionBar } from '@/components/attribution/AttributionBar'
import { ContributorAvatarStack } from '@/components/ui/ContributorAvatar'
import { Badge } from '@/components/ui/Badge'
import { ArrowRight } from 'lucide-react'

interface MemoryCardProps {
  memory: Memory
}

export function MemoryCard({ memory }: MemoryCardProps) {
  const contributors = memory.attribution?.contributors ?? []
  const contributorNames = contributors.map((c) => c.name)

  return (
    <Link href={`/memories/${memory.id}`}>
      <div className="group bg-surface border border-border rounded-xl p-5 card-hover cursor-pointer flex flex-col gap-3 h-full">
        {/* Header */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            {memory.memory_type && (
              <Badge variant="purple">{memory.memory_type.toUpperCase()}</Badge>
            )}
            {memory.tags?.slice(0, 2).map((tag) => (
              <Badge key={tag} variant="gray">{tag}</Badge>
            ))}
          </div>
          {memory.current_version && (
            <Badge variant="green">LATEST</Badge>
          )}
        </div>

        {/* Content */}
        <p className="text-primary text-sm leading-relaxed flex-1 line-clamp-3">
          {truncate(memory.content, 200)}
        </p>

        {/* Attribution bar */}
        {contributors.length > 0 && (
          <AttributionBar contributors={contributors} height={4} />
        )}

        {/* Footer */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ContributorAvatarStack names={contributorNames} max={3} />
            <span className="text-xs text-muted font-mono">
              {formatRelativeTime(memory.created_at)}
            </span>
          </div>
          <ArrowRight className="w-3.5 h-3.5 text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
        </div>
      </div>
    </Link>
  )
}

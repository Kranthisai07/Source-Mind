'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ChevronLeft, Sparkles, ShieldAlert } from 'lucide-react'
import Link from 'next/link'
import { TopBar } from '@/components/layout/TopBar'
import { PageWrapper } from '@/components/layout/PageWrapper'
import { Button } from '@/components/ui/Button'
import { Badge, ConflictStatusBadge } from '@/components/ui/Badge'
import { SkeletonCard, Skeleton } from '@/components/ui/SkeletonCard'
import { useConflict, useReviewConflictMutation, useResolveConflictMutation } from '@/lib/queries/useConflicts'
import { formatDate, cn } from '@/lib/utils'
import type { ResolutionType } from '@/lib/types'

const RESOLUTION_OPTIONS: {
  type: ResolutionType
  title: string
  description: string
  accent: string
}[] = [
  { type: 'kept_a',  title: 'Keep Memory A',   description: 'Deprecate Memory B',        accent: '#4F8EF7' },
  { type: 'kept_b',  title: 'Keep Memory B',   description: 'Deprecate Memory A',        accent: '#8B5CF6' },
  { type: 'merged',  title: 'Merge Both',       description: 'Create new merged memory',  accent: '#10B981' },
  { type: 'split',   title: 'Split Context',    description: 'Tag each differently',       accent: '#F59E0B' },
  { type: 'deferred',title: 'Defer Decision',  description: 'Set revisit date',           accent: '#8888A0' },
]

export default function ConflictDetailPage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const conflictId = params.id

  const { data: conflict, isLoading } = useConflict(conflictId)
  const reviewMutation = useReviewConflictMutation()
  const resolveMutation = useResolveConflictMutation()

  const [selectedType, setSelectedType] = useState<ResolutionType | null>(null)
  const [note, setNote] = useState('')
  const [mergedContent, setMergedContent] = useState('')
  const [tagA, setTagA] = useState('')
  const [tagB, setTagB] = useState('')
  const [revisitAt, setRevisitAt] = useState('')

  const isOpen = conflict?.status === 'open'
  const isUnderReview = conflict?.status === 'under_review'
  const canResolve = isOpen || isUnderReview

  async function handleResolve() {
    if (!selectedType) return
    await resolveMutation.mutateAsync({
      conflictId,
      body: {
        resolution_type: selectedType,
        resolution_note: note || undefined,
        merged_content: mergedContent || undefined,
        tag_a: tagA || undefined,
        tag_b: tagB || undefined,
        revisit_at: revisitAt || undefined,
      },
    })
    setTimeout(() => router.push('/conflicts'), 1500)
  }

  const memA = conflict?.memory_a?.content ?? conflict?.memory_a_content ?? ''
  const memB = conflict?.memory_b?.content ?? conflict?.memory_b_content ?? ''
  const suggestion = conflict?.suggested_resolution

  return (
    <PageWrapper>
      <TopBar
        title="Conflict Review"
        subtitle={conflict ? `Score: ${(conflict.conflict_score ?? conflict.similarity_score ?? 0).toFixed(2)} · ${conflict.conflict_type}` : undefined}
        actions={
          <Link href="/conflicts">
            <Button variant="ghost" size="sm"><ChevronLeft className="w-3.5 h-3.5" /> Back</Button>
          </Link>
        }
      />

      <div className="px-8 py-6 space-y-6">
        {isLoading ? (
          <div className="space-y-4"><SkeletonCard /><SkeletonCard /></div>
        ) : !conflict ? (
          <div className="text-center py-20 text-muted">Conflict not found</div>
        ) : (
          <>
            {/* Status header */}
            <div className="flex items-center gap-3">
              <ConflictStatusBadge status={conflict.status} />
              {conflict.explanation && (
                <p className="text-sm text-secondary">{conflict.explanation}</p>
              )}
            </div>

            {/* AI Advisory */}
            {suggestion ? (
              <div className="p-5 bg-[rgba(139,92,246,0.06)] border border-[rgba(139,92,246,0.2)] rounded-xl">
                <div className="flex items-center gap-2 mb-3">
                  <Sparkles className="w-4 h-4 text-accent-purple" />
                  <span className="text-sm font-medium text-accent-purple">AI Advisory (non-binding)</span>
                  <Badge variant="gray" className="ml-auto">
                    {(suggestion.confidence * 100).toFixed(0)}% confidence
                  </Badge>
                </div>
                <p className="text-sm text-secondary mb-2">{suggestion.reasoning}</p>
                <div className="flex items-center gap-2">
                  <Badge variant="purple">Suggested: {suggestion.resolution_type.replace('_', ' ').toUpperCase()}</Badge>
                  <span className="text-xs text-muted">Human decision required</span>
                </div>
              </div>
            ) : (
              <div className="p-5 bg-surface border border-border rounded-xl">
                <div className="flex items-center gap-2">
                  <Skeleton className="w-4 h-4 rounded" />
                  <span className="text-sm text-muted">Generating AI advisory...</span>
                </div>
              </div>
            )}

            {/* Side-by-side memories */}
            <div className="grid grid-cols-2 gap-4 relative">
              <div className="bg-surface border border-[rgba(79,142,247,0.3)] rounded-xl p-5">
                <div className="flex items-center justify-between mb-3">
                  <Badge variant="blue">MEMORY A</Badge>
                  <span className="text-xs text-muted font-mono">
                    {conflict.memory_a_id?.slice(0, 8)}
                  </span>
                </div>
                <p className="text-sm text-primary leading-relaxed">{memA}</p>
              </div>

              {/* VS divider */}
              <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-10">
                <div className="w-8 h-8 rounded-full bg-elevated border border-border flex items-center justify-center">
                  <span className="text-xs font-mono text-muted">VS</span>
                </div>
              </div>

              <div className="bg-surface border border-[rgba(139,92,246,0.3)] rounded-xl p-5">
                <div className="flex items-center justify-between mb-3">
                  <Badge variant="purple">MEMORY B</Badge>
                  <span className="text-xs text-muted font-mono">
                    {conflict.memory_b_id?.slice(0, 8)}
                  </span>
                </div>
                <p className="text-sm text-primary leading-relaxed">{memB}</p>
              </div>
            </div>

            {/* Resolution panel */}
            {canResolve && (
              <div className="bg-surface border border-border rounded-xl p-6 space-y-5">
                {isOpen && (
                  <div className="flex items-center justify-between">
                    <p className="text-sm text-secondary">Start reviewing this conflict to begin resolution.</p>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => reviewMutation.mutate(conflictId)}
                      loading={reviewMutation.isPending}
                    >
                      <ShieldAlert className="w-3.5 h-3.5" /> Start Review
                    </Button>
                  </div>
                )}

                {isUnderReview && (
                  <>
                    <h3 className="text-sm font-medium text-primary">Choose a resolution</h3>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                      {RESOLUTION_OPTIONS.map(({ type, title, description, accent }) => (
                        <button
                          key={type}
                          onClick={() => setSelectedType(type)}
                          className={cn(
                            'text-left p-4 rounded-lg border transition-all duration-150',
                            selectedType === type
                              ? 'border-current bg-[rgba(255,255,255,0.04)]'
                              : 'border-border hover:border-border-active'
                          )}
                          style={selectedType === type ? { borderColor: accent, color: accent } : {}}
                        >
                          <p className="font-medium text-sm">{title}</p>
                          <p className="text-xs text-muted mt-0.5">{description}</p>
                        </button>
                      ))}
                    </div>

                    {/* Conditional fields */}
                    {selectedType === 'merged' && (
                      <div>
                        <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
                          Merged Content
                        </label>
                        <textarea
                          value={mergedContent}
                          onChange={(e) => setMergedContent(e.target.value)}
                          placeholder={suggestion?.resolution_type === 'merged' ? 'Pre-fill from AI suggestion...' : 'Write the merged memory content...'}
                          className="w-full bg-bg border border-border rounded-lg p-3 text-sm text-primary placeholder-muted focus:border-accent-blue focus:outline-none resize-none"
                          rows={4}
                        />
                      </div>
                    )}

                    {selectedType === 'split' && (
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="text-xs text-secondary font-mono mb-1.5 block">Tag for Memory A</label>
                          <input value={tagA} onChange={(e) => setTagA(e.target.value)}
                            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-primary focus:border-accent-blue focus:outline-none"
                            placeholder="context-a" />
                        </div>
                        <div>
                          <label className="text-xs text-secondary font-mono mb-1.5 block">Tag for Memory B</label>
                          <input value={tagB} onChange={(e) => setTagB(e.target.value)}
                            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-primary focus:border-accent-blue focus:outline-none"
                            placeholder="context-b" />
                        </div>
                      </div>
                    )}

                    {selectedType === 'deferred' && (
                      <div>
                        <label className="text-xs text-secondary font-mono mb-1.5 block">Revisit Date</label>
                        <input type="date" value={revisitAt} onChange={(e) => setRevisitAt(e.target.value)}
                          className="bg-bg border border-border rounded-lg px-3 py-2 text-sm text-primary focus:border-accent-blue focus:outline-none" />
                      </div>
                    )}

                    <div>
                      <label className="text-xs text-secondary font-mono mb-1.5 block">Resolution Note</label>
                      <input value={note} onChange={(e) => setNote(e.target.value)}
                        className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-primary placeholder-muted focus:border-accent-blue focus:outline-none"
                        placeholder="Why was this resolution chosen?" />
                    </div>

                    <Button
                      onClick={handleResolve}
                      loading={resolveMutation.isPending}
                      disabled={!selectedType}
                    >
                      Resolve Conflict
                    </Button>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </PageWrapper>
  )
}

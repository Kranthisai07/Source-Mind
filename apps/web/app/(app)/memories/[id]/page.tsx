'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { Pencil, Trash2, ChevronLeft, Tag, Clock, AlertTriangle } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { PageWrapper } from '@/components/layout/PageWrapper'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { SkeletonCard, Skeleton } from '@/components/ui/SkeletonCard'
import { AttributionBar } from '@/components/attribution/AttributionBar'
import { ContributorAvatar } from '@/components/ui/ContributorAvatar'
import { VersionTimeline } from '@/components/memory/VersionTimeline'
import {
  useMemory, useMemoryVersions, useMemoryAttribution,
  useUpdateMemoryMutation, useDeleteMemoryMutation,
} from '@/lib/queries/useMemories'
import { formatDate, formatRelativeTime, formatPct, cn } from '@/lib/utils'
import type { MemoryVersion } from '@/lib/types'
import Link from 'next/link'

export default function MemoryDetailPage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const id = params.id

  const { data: memory, isLoading } = useMemory(id)
  const { data: versions } = useMemoryVersions(id)
  const { data: attribution } = useMemoryAttribution(id)
  const update = useUpdateMemoryMutation(id)
  const deleteMutation = useDeleteMemoryMutation()

  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [selectedVersion, setSelectedVersion] = useState<MemoryVersion | null>(null)

  const displayContent = selectedVersion?.content ?? memory?.content ?? ''
  const contributors = attribution?.contributors ?? memory?.attribution?.contributors ?? []

  async function handleSave() {
    await update.mutateAsync(editContent)
    setEditing(false)
  }

  async function handleDelete() {
    await deleteMutation.mutateAsync(id)
    router.push('/memories')
  }

  return (
    <PageWrapper>
      <TopBar
        title="Memory Detail"
        subtitle={memory ? `v${memory.version} · ${formatRelativeTime(memory.created_at)}` : undefined}
        actions={
          <Link href="/memories">
            <Button variant="ghost" size="sm"><ChevronLeft className="w-3.5 h-3.5" /> Back</Button>
          </Link>
        }
      />

      <div className="px-8 py-6">
        {isLoading ? (
          <div className="grid grid-cols-5 gap-6">
            <div className="col-span-3 space-y-4"><SkeletonCard /><SkeletonCard /></div>
            <div className="col-span-2 space-y-4"><SkeletonCard /></div>
          </div>
        ) : !memory ? (
          <div className="text-center py-20 text-muted">Memory not found</div>
        ) : (
          <div className="grid grid-cols-5 gap-6">
            {/* Left: content */}
            <div className="col-span-3 space-y-6">
              {/* Not-current warning */}
              {!memory.current_version && (
                <div className="flex items-center gap-2 px-4 py-3 bg-[rgba(245,158,11,0.08)] border border-[rgba(245,158,11,0.2)] rounded-lg">
                  <AlertTriangle className="w-4 h-4 text-accent-amber flex-shrink-0" />
                  <p className="text-sm text-accent-amber">
                    This is an older version.{' '}
                    <Link href={`/memories/${id}`} className="underline">View current →</Link>
                  </p>
                </div>
              )}

              {/* Tags & type */}
              <div className="flex items-center gap-2 flex-wrap">
                {memory.memory_type && <Badge variant="purple">{memory.memory_type.toUpperCase()}</Badge>}
                {memory.tags?.map((t) => <Badge key={t} variant="gray">{t}</Badge>)}
              </div>

              {/* Content */}
              <div className="bg-surface border border-border rounded-xl p-6">
                {editing ? (
                  <div className="space-y-4">
                    <textarea
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      className="w-full bg-bg border border-border rounded-lg p-3 text-sm text-primary focus:border-accent-blue focus:outline-none resize-none transition-colors min-h-48"
                      autoFocus
                    />
                    <div className="flex gap-3">
                      <Button onClick={handleSave} loading={update.isPending} size="sm">Save</Button>
                      <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <p className="text-primary text-sm leading-relaxed whitespace-pre-wrap">{displayContent}</p>
                    <div className="flex gap-2 mt-5">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => { setEditContent(memory.content); setEditing(true) }}
                      >
                        <Pencil className="w-3.5 h-3.5" /> Edit
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => setConfirmDelete(true)}
                      >
                        <Trash2 className="w-3.5 h-3.5" /> Delete
                      </Button>
                    </div>
                  </div>
                )}
              </div>

              {/* Version timeline */}
              {versions && versions.length > 1 && (
                <div className="bg-surface border border-border rounded-xl p-6">
                  <VersionTimeline
                    versions={versions}
                    currentId={selectedVersion?.id ?? id}
                    onSelect={setSelectedVersion}
                  />
                </div>
              )}
            </div>

            {/* Right: metadata */}
            <div className="col-span-2 space-y-4">
              {/* Attribution */}
              <div className="bg-surface border border-border rounded-xl p-5">
                <h3 className="text-xs text-secondary font-mono uppercase tracking-wider mb-4">
                  Attribution
                </h3>
                {contributors.length > 0 ? (
                  <>
                    <AttributionBar contributors={contributors} height={10} />
                    <div className="mt-4 space-y-3">
                      {contributors.map((c) => (
                        <div key={c.id} className="flex items-center gap-3">
                          <ContributorAvatar name={c.name} size="sm" />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm text-primary truncate">{c.name}</p>
                            <p className="text-xs text-muted font-mono">{c.contribution_type}</p>
                          </div>
                          <span className="text-sm font-mono text-secondary">
                            {formatPct(c.contribution_pct)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="text-muted text-sm">No attribution data</p>
                )}
              </div>

              {/* Metadata */}
              <div className="bg-surface border border-border rounded-xl p-5 space-y-3">
                <h3 className="text-xs text-secondary font-mono uppercase tracking-wider mb-1">
                  Metadata
                </h3>
                <div className="flex items-center gap-2">
                  <Clock className="w-3.5 h-3.5 text-muted" />
                  <span className="text-xs text-secondary">Created {formatDate(memory.created_at)}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Tag className="w-3.5 h-3.5 text-muted" />
                  <span className="text-xs text-secondary">Version {memory.version}</span>
                </div>
                {memory.importance_score !== undefined && (
                  <div>
                    <div className="flex justify-between mb-1">
                      <span className="text-xs text-secondary">Importance</span>
                      <span className="text-xs font-mono text-muted">
                        {(memory.importance_score * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className="h-1.5 bg-border rounded-full overflow-hidden">
                      <div
                        className="h-full bg-accent-purple rounded-full"
                        style={{ width: `${memory.importance_score * 100}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete memory?"
        description="This cannot be undone. The memory and all its version history will be permanently removed."
        confirmLabel="Delete"
        onConfirm={handleDelete}
        loading={deleteMutation.isPending}
      />
    </PageWrapper>
  )
}

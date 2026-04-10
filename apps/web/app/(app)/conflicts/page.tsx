'use client'

import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { PageWrapper } from '@/components/layout/PageWrapper'
import { ConflictCard } from '@/components/conflict/ConflictCard'
import { SkeletonCard } from '@/components/ui/SkeletonCard'
import { EmptyState } from '@/components/ui/EmptyState'
import { useConflicts } from '@/lib/queries/useConflicts'
import { useAppStore } from '@/lib/store/useAppStore'
import { cn } from '@/lib/utils'

const STATUS_TABS = [
  { value: '',              label: 'All' },
  { value: 'open',          label: 'Open' },
  { value: 'under_review',  label: 'Under Review' },
  { value: 'resolved',      label: 'Resolved' },
  { value: 'deferred',      label: 'Deferred' },
]

export default function ConflictsPage() {
  const { activeWorkspace } = useAppStore()
  const wsId = activeWorkspace?.id ?? ''
  const [activeTab, setActiveTab] = useState('')

  const { data, isLoading } = useConflicts(wsId, activeTab || undefined)
  const conflicts = data?.conflicts ?? []

  return (
    <PageWrapper>
      <TopBar
        title="Knowledge Conflicts"
        subtitle={data ? `${data.total ?? conflicts.length} conflict${conflicts.length !== 1 ? 's' : ''}` : undefined}
      />

      <div className="px-8 py-6 space-y-6">
        {/* Tab bar */}
        <div className="flex gap-1 border-b border-border">
          {STATUS_TABS.map(({ value, label }) => (
            <button
              key={value}
              onClick={() => setActiveTab(value)}
              className={cn(
                'px-4 py-2.5 text-sm transition-colors border-b-2 -mb-px',
                activeTab === value
                  ? 'text-primary border-accent-blue'
                  : 'text-secondary border-transparent hover:text-primary'
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {/* List */}
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : conflicts.length === 0 ? (
          <EmptyState
            icon={AlertTriangle}
            title="No conflicts found"
            description={
              activeTab
                ? `No ${activeTab.replace('_', ' ')} conflicts in this workspace.`
                : 'No knowledge conflicts detected. Your team is aligned!'
            }
          />
        ) : (
          <div className="space-y-3">
            {conflicts.map((c) => <ConflictCard key={c.id} conflict={c} />)}
          </div>
        )}
      </div>
    </PageWrapper>
  )
}

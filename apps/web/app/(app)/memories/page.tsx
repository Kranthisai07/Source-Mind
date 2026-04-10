'use client'

import { useState, useCallback } from 'react'
import { useDebouncedCallback } from 'use-debounce'
import { Search, Plus, Filter, Brain } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { PageWrapper } from '@/components/layout/PageWrapper'
import { Button } from '@/components/ui/Button'
import { MemoryCard } from '@/components/memory/MemoryCard'
import { IngestForm } from '@/components/memory/IngestForm'
import { JobStatusTracker } from '@/components/memory/JobStatusTracker'
import { SkeletonCard } from '@/components/ui/SkeletonCard'
import { EmptyState } from '@/components/ui/EmptyState'
import { useSearch } from '@/lib/queries/useMemories'
import { useAppStore } from '@/lib/store/useAppStore'
import { cn } from '@/lib/utils'
import type { SearchFilters } from '@/lib/types'

const MODES = ['hybrid', 'semantic', 'keyword'] as const
type Mode = typeof MODES[number]

export default function MemoriesPage() {
  const { activeWorkspace } = useAppStore()
  const wsId = activeWorkspace?.id ?? ''

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [mode, setMode] = useState<Mode>('hybrid')
  const [ingestOpen, setIngestOpen] = useState(false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)

  const filters: SearchFilters = {
    workspace_ids: wsId ? [wsId] : [],
    mode,
    limit: 40,
    include_attribution: true,
  }

  const debounce = useDebouncedCallback((v: string) => setDebouncedQuery(v), 300)

  const { data: searchData, isLoading, isFetching } = useSearch(debouncedQuery, filters)
  const results = searchData?.results ?? []

  return (
    <PageWrapper>
      <TopBar
        title="Memories"
        subtitle={searchData ? `${searchData.total_found} results · ${searchData.search_latency_ms}ms` : undefined}
        actions={
          <Button onClick={() => setIngestOpen(true)} size="sm">
            <Plus className="w-3.5 h-3.5" /> Ingest
          </Button>
        }
      />

      <div className="px-8 py-6 space-y-4">
        {/* Search bar */}
        <div className="relative">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted" />
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); debounce(e.target.value) }}
            placeholder="Search your team's knowledge..."
            className="w-full bg-surface border border-border rounded-xl pl-11 pr-36 py-3 text-sm text-primary placeholder-muted focus:border-accent-blue focus:outline-none transition-colors"
          />
          {/* Mode pills */}
          <div className="absolute right-3 top-1/2 -translate-y-1/2 flex gap-1">
            {MODES.map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={cn(
                  'text-xs px-2.5 py-1 rounded-md font-mono transition-all',
                  mode === m
                    ? 'bg-accent-blue text-white'
                    : 'text-muted hover:text-secondary'
                )}
              >
                {m}
              </button>
            ))}
          </div>
        </div>

        {/* Results */}
        {isLoading || isFetching ? (
          <div className="grid grid-cols-2 gap-4">
            {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : results.length === 0 && debouncedQuery.length > 2 ? (
          <EmptyState
            icon={Brain}
            title="No memories found"
            description={`No results for "${debouncedQuery}". Try a different query or ingest new content.`}
            action={<Button onClick={() => setIngestOpen(true)} size="sm"><Plus className="w-3.5 h-3.5" /> Ingest content</Button>}
          />
        ) : results.length === 0 && debouncedQuery.length <= 2 ? (
          <EmptyState
            icon={Brain}
            title="Search your team's knowledge"
            description="Type at least 3 characters to search, or ingest your first document to get started."
            action={<Button onClick={() => setIngestOpen(true)}><Plus className="w-3.5 h-3.5" /> Ingest first document</Button>}
          />
        ) : (
          <div className="grid grid-cols-2 gap-4">
            {results.map((result) => (
              <MemoryCard
                key={result.memory_id}
                memory={{
                  id: result.memory_id,
                  content: result.content,
                  tags: result.tags,
                  workspace_id: result.workspace_id,
                  version: 1,
                  current_version: result.is_latest,
                  created_at: result.created_at,
                  attribution: result.attribution,
                  is_latest: result.is_latest,
                }}
              />
            ))}
          </div>
        )}
      </div>

      <IngestForm
        open={ingestOpen}
        onClose={() => setIngestOpen(false)}
        onJobCreated={(id) => setActiveJobId(id)}
      />

      {activeJobId && (
        <JobStatusTracker
          jobId={activeJobId}
          open={!!activeJobId}
          onClose={() => setActiveJobId(null)}
        />
      )}
    </PageWrapper>
  )
}

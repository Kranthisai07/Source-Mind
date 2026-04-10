'use client'

import { BarChart2 } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { PageWrapper } from '@/components/layout/PageWrapper'
import { SkeletonCard } from '@/components/ui/SkeletonCard'
import { EmptyState } from '@/components/ui/EmptyState'
import { HealthScoreGauge } from '@/components/analytics/HealthScoreGauge'
import { ContributionChart } from '@/components/analytics/ContributionChart'
import { ContributorMap } from '@/components/analytics/ContributorMap'
import { KnowledgeGaps } from '@/components/analytics/KnowledgeGaps'
import { useAnalyticsOverview, useContributionMap, useKnowledgeGaps } from '@/lib/queries/useAnalytics'
import { useAppStore } from '@/lib/store/useAppStore'
import { formatRelativeTime } from '@/lib/utils'

function StatCard({ label, value, sub }: { label: string; value: number | string; sub?: string }) {
  return (
    <div className="bg-surface border border-border rounded-xl p-5">
      <p className="text-xs text-muted font-mono uppercase tracking-wider mb-2">{label}</p>
      <p className="font-display text-3xl font-medium text-primary">{value}</p>
      {sub && <p className="text-xs text-secondary mt-1">{sub}</p>}
    </div>
  )
}

export default function AnalyticsPage() {
  const { activeWorkspace } = useAppStore()
  const wsId = activeWorkspace?.id ?? ''

  const { data: overview, isLoading: overviewLoading } = useAnalyticsOverview(wsId)
  const { data: contribData, isLoading: contribLoading } = useContributionMap(wsId)
  const { data: gapsData, isLoading: gapsLoading } = useKnowledgeGaps(wsId)

  const contributors = contribData?.contributors ?? []
  const gaps = gapsData?.gaps ?? []

  if (!wsId) {
    return (
      <PageWrapper>
        <TopBar title="Analytics" />
        <div className="px-8 py-6">
          <EmptyState icon={BarChart2} title="No workspace selected" description="Select a workspace to view analytics." />
        </div>
      </PageWrapper>
    )
  }

  return (
    <PageWrapper>
      <TopBar
        title="Analytics"
        subtitle={overview ? `Last updated ${formatRelativeTime(new Date().toISOString())}` : undefined}
      />

      <div className="px-8 py-6 space-y-8">
        {/* ── Stat row ─────────────────────────────────────────────── */}
        {overviewLoading ? (
          <div className="grid grid-cols-4 gap-4">
            {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : overview ? (
          <div className="grid grid-cols-4 gap-4">
            <StatCard
              label="Total Memories"
              value={overview.total_memories.toLocaleString()}
            />
            <StatCard
              label="Contributors"
              value={overview.total_contributors}
            />
            <StatCard
              label="Last 30 days"
              value={overview.memories_created_last_30_days}
              sub="new memories"
            />
            <StatCard
              label="Open Conflicts"
              value={overview.open_conflicts}
              sub={overview.open_conflicts === 0 ? 'all clear' : 'need review'}
            />
          </div>
        ) : null}

        {/* ── Health + Contribution chart ───────────────────────── */}
        <div className="grid grid-cols-3 gap-6">
          {/* Health gauge */}
          <div className="bg-surface border border-border rounded-xl p-6 flex flex-col items-center">
            <h3 className="text-xs text-secondary font-mono uppercase tracking-wider mb-5 self-start">
              Knowledge Health
            </h3>
            {overviewLoading ? (
              <SkeletonCard />
            ) : overview ? (
              <HealthScoreGauge
                score={overview.knowledge_health_score}
                breakdown={overview.health_breakdown}
              />
            ) : null}
          </div>

          {/* Contribution activity chart */}
          <div className="col-span-2 bg-surface border border-border rounded-xl p-6">
            <h3 className="text-xs text-secondary font-mono uppercase tracking-wider mb-5">
              Contribution Activity
            </h3>
            {overviewLoading || contribLoading ? (
              <div className="h-48"><SkeletonCard /></div>
            ) : overview?.top_contributors?.length ? (
              <ContributionChart contributors={overview.top_contributors} />
            ) : (
              <div className="h-48 flex items-center justify-center text-muted text-sm">
                No contribution data yet
              </div>
            )}
          </div>
        </div>

        {/* ── Contributor Map ───────────────────────────────────── */}
        <div className="bg-surface border border-border rounded-xl p-6">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-xs text-secondary font-mono uppercase tracking-wider">
              Contributor Map
            </h3>
            {contributors.length > 0 && (
              <span className="text-xs text-muted font-mono">{contributors.length} contributors</span>
            )}
          </div>
          {contribLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : (
            <ContributorMap contributors={contributors} />
          )}
        </div>

        {/* ── Knowledge Gaps ────────────────────────────────────── */}
        <div className="bg-surface border border-border rounded-xl p-6">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-xs text-secondary font-mono uppercase tracking-wider">
              Knowledge Gaps
            </h3>
            {gaps.length > 0 && (
              <span className="text-xs text-muted font-mono">{gaps.length} {gaps.length === 1 ? 'gap' : 'gaps'} found</span>
            )}
          </div>
          {gapsLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : (
            <KnowledgeGaps gaps={gaps} />
          )}
        </div>
      </div>
    </PageWrapper>
  )
}

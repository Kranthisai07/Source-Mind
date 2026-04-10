'use client'

import dynamic from 'next/dynamic'
import { useUser } from '@clerk/nextjs'
import { Brain, Users, TrendingUp, AlertTriangle, ChevronRight } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { PageWrapper } from '@/components/layout/PageWrapper'
import { ActivityFeed } from '@/components/analytics/ActivityFeed'
import { useAnalyticsOverview, useKnowledgeGaps } from '@/lib/queries/useAnalytics'
import { useAppStore } from '@/lib/store/useAppStore'
import { MetricCardSkeleton, Skeleton } from '@/components/ui/SkeletonCard'
import { riskColor, riskBg, CONTRIB_COLORS } from '@/lib/utils'
import Link from 'next/link'

const HealthScoreGauge = dynamic(
  () => import('@/components/analytics/HealthScoreGauge').then((m) => m.HealthScoreGauge),
  { ssr: false, loading: () => <Skeleton className="w-48 h-48 rounded-full mx-auto" /> }
)

const ContributorsChart = dynamic(
  () => import('@/components/analytics/ContributorsBarChart'),
  { ssr: false, loading: () => <Skeleton className="h-44 w-full rounded-lg" /> }
)

function MetricCard({
  icon: Icon,
  label,
  value,
  accent,
}: {
  icon: React.ElementType
  label: string
  value: string | number
  accent?: string
}) {
  return (
    <div className="bg-surface border border-border rounded-xl p-5 hover:border-border-active transition-colors">
      <div className="flex items-center gap-2 mb-3">
        <Icon className="w-4 h-4 text-muted" />
        <span className="text-xs text-secondary font-mono uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-3xl font-display font-medium" style={accent ? { color: accent } : {}}>
        {value}
      </div>
    </div>
  )
}

export default function DashboardPage() {
  const { user } = useUser()
  const { activeWorkspace } = useAppStore()
  const wsId = activeWorkspace?.id ?? ''

  const { data: overview, isLoading: overviewLoading } = useAnalyticsOverview(wsId)
  const { data: gapsData } = useKnowledgeGaps(wsId)

  const firstName = user?.firstName ?? user?.username ?? 'there'
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'

  const chartData = overview?.top_contributors?.map((c, i) => ({
    name: c.name.split(' ')[0],
    count: c.count,
    color: CONTRIB_COLORS[i % CONTRIB_COLORS.length],
  })) ?? []

  return (
    <PageWrapper>
      <TopBar
        title={`${greeting}, ${firstName}`}
        subtitle={activeWorkspace ? `${activeWorkspace.name} · Knowledge overview` : undefined}
      />

      <div className="px-8 py-6 space-y-8">
        {/* Metric cards */}
        <div className="grid grid-cols-4 gap-4">
          {overviewLoading ? (
            Array.from({ length: 4 }).map((_, i) => <MetricCardSkeleton key={i} />)
          ) : (
            <>
              <MetricCard icon={Brain}       label="Total Memories"     value={overview?.total_memories ?? 0} />
              <MetricCard icon={Users}       label="Contributors"       value={overview?.total_contributors ?? 0} />
              <MetricCard icon={TrendingUp}  label="New This Month"     value={overview?.memories_created_last_30_days ?? 0} />
              <MetricCard
                icon={AlertTriangle}
                label="Open Conflicts"
                value={overview?.open_conflicts ?? 0}
                accent={(overview?.open_conflicts ?? 0) > 0 ? '#F59E0B' : undefined}
              />
            </>
          )}
        </div>

        {/* Health score + Activity */}
        <div className="grid grid-cols-5 gap-6">
          <div className="col-span-3 bg-surface border border-border rounded-xl p-6">
            <h3 className="text-sm font-medium text-secondary mb-6">Knowledge Health Score</h3>
            {overviewLoading ? (
              <div className="flex flex-col items-center gap-4">
                <Skeleton className="w-48 h-48 rounded-full" />
              </div>
            ) : (
              <HealthScoreGauge
                score={overview?.knowledge_health_score ?? 0}
                size={180}
                breakdown={overview?.health_breakdown}
              />
            )}
          </div>

          <div className="col-span-2 bg-surface border border-border rounded-xl p-6">
            <h3 className="text-sm font-medium text-secondary mb-4">Recent Activity</h3>
            {overviewLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="flex gap-3 items-start">
                    <Skeleton className="w-2 h-2 rounded-full mt-2 flex-shrink-0" />
                    <Skeleton className="h-4 flex-1 rounded" />
                  </div>
                ))}
              </div>
            ) : (
              <ActivityFeed events={overview?.recent_activity ?? []} />
            )}
          </div>
        </div>

        {/* Top contributors chart + Knowledge gaps */}
        <div className="grid grid-cols-2 gap-6">
          <div className="bg-surface border border-border rounded-xl p-6">
            <h3 className="text-sm font-medium text-secondary mb-4">Top Contributors</h3>
            {overviewLoading || chartData.length === 0 ? (
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-3">
                    <Skeleton className="h-3 w-20 rounded" />
                    <Skeleton className="h-6 rounded flex-1" />
                  </div>
                ))}
              </div>
            ) : (
              <ContributorsChart data={chartData} />
            )}
          </div>

          <div className="bg-surface border border-border rounded-xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-medium text-secondary">Knowledge Gaps</h3>
              <Link href="/analytics" className="text-xs text-accent-blue hover:underline flex items-center gap-0.5">
                View all <ChevronRight className="w-3 h-3" />
              </Link>
            </div>
            {gapsData?.gaps?.length === 0 ? (
              <p className="text-muted text-sm">No gaps detected. Great work!</p>
            ) : (
              <div className="space-y-3">
                {(gapsData?.gaps ?? []).slice(0, 3).map((gap, i) => (
                  <div
                    key={i}
                    className="p-3 rounded-lg border"
                    style={{ borderColor: riskColor(gap.risk_level) + '33', background: riskBg(gap.risk_level) }}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className="text-xs font-mono px-1.5 py-0.5 rounded"
                        style={{ color: riskColor(gap.risk_level), background: riskColor(gap.risk_level) + '22' }}
                      >
                        {gap.risk_level}
                      </span>
                      <span className="text-xs text-secondary truncate">{gap.gap_type.replace(/_/g, ' ')}</span>
                    </div>
                    <p className="text-sm text-primary line-clamp-2">{gap.description}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </PageWrapper>
  )
}

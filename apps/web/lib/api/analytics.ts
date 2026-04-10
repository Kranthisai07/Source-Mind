import apiClient from './client'
import type { AnalyticsOverview, ContributorMapEntry, KnowledgeGap } from '@/lib/types'

export const analyticsApi = {
  overview: (workspaceId: string) =>
    apiClient
      .get<AnalyticsOverview>(`/v1/workspaces/${workspaceId}/analytics/overview`)
      .then((r) => r.data),

  contributionMap: (workspaceId: string) =>
    apiClient
      .get<{ contributors: ContributorMapEntry[] }>(
        `/v1/workspaces/${workspaceId}/analytics/contribution-map`
      )
      .then((r) => r.data),

  knowledgeGaps: (workspaceId: string) =>
    apiClient
      .get<{ gaps: KnowledgeGap[] }>(`/v1/workspaces/${workspaceId}/analytics/knowledge-gaps`)
      .then((r) => r.data),
}

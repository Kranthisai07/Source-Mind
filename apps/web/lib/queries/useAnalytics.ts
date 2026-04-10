'use client'

import { useQuery } from '@tanstack/react-query'
import { analyticsApi } from '@/lib/api/analytics'

export const analyticsKeys = {
  overview: (workspaceId: string) => ['analytics', 'overview', workspaceId] as const,
  contributionMap: (workspaceId: string) => ['analytics', 'contribution-map', workspaceId] as const,
  gaps: (workspaceId: string) => ['analytics', 'gaps', workspaceId] as const,
}

export function useAnalyticsOverview(workspaceId: string) {
  return useQuery({
    queryKey: analyticsKeys.overview(workspaceId),
    queryFn: () => analyticsApi.overview(workspaceId),
    enabled: !!workspaceId,
    staleTime: 60_000,
  })
}

export function useContributionMap(workspaceId: string) {
  return useQuery({
    queryKey: analyticsKeys.contributionMap(workspaceId),
    queryFn: () => analyticsApi.contributionMap(workspaceId),
    enabled: !!workspaceId,
    staleTime: 120_000,
  })
}

export function useKnowledgeGaps(workspaceId: string) {
  return useQuery({
    queryKey: analyticsKeys.gaps(workspaceId),
    queryFn: () => analyticsApi.knowledgeGaps(workspaceId),
    enabled: !!workspaceId,
    staleTime: 120_000,
  })
}

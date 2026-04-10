import apiClient from './client'
import type { Conflict, ResolutionType } from '@/lib/types'

export interface ConflictListResponse {
  conflicts: Conflict[]
  total: number
  next_cursor?: string
}

export interface ResolveBody {
  resolution_type: ResolutionType
  resolution_note?: string
  merged_content?: string
  revisit_at?: string
  tag_a?: string
  tag_b?: string
}

export const conflictsApi = {
  list: (workspaceId: string, params?: { status?: string; limit?: number; cursor?: string }) =>
    apiClient
      .get<ConflictListResponse>(`/v1/workspaces/${workspaceId}/conflicts`, { params })
      .then((r) => r.data),

  get: (conflictId: string) =>
    apiClient.get<Conflict>(`/v1/conflicts/${conflictId}`).then((r) => r.data),

  review: (conflictId: string) =>
    apiClient.post<{ status: string }>(`/v1/conflicts/${conflictId}/review`).then((r) => r.data),

  resolve: (conflictId: string, body: ResolveBody) =>
    apiClient
      .post<{ status: string; resolution_type: string }>(`/v1/conflicts/${conflictId}/resolve`, body)
      .then((r) => r.data),
}

import apiClient from './client'
import type { HandoffSummary } from '@/lib/types'

export const handoffApi = {
  initiate: (workspaceId: string, departing_user_id: string) =>
    apiClient
      .post<HandoffSummary>(`/v1/workspaces/${workspaceId}/handoff/initiate`, {
        departing_user_id,
      })
      .then((r) => r.data),

  assign: (
    workspaceId: string,
    body: {
      memory_id: string
      new_owner_id: string
      handoff_record_id: string
      note?: string
    }
  ) =>
    apiClient
      .post<{ memory_id: string; attribution: unknown[] }>(
        `/v1/workspaces/${workspaceId}/handoff/assign`,
        body
      )
      .then((r) => r.data),

  complete: (workspaceId: string, departing_user_id: string, handoff_record_id: string) =>
    apiClient
      .post<{ status: string; unassigned_tier_1_count: number }>(
        `/v1/workspaces/${workspaceId}/handoff/complete`,
        { departing_user_id, handoff_record_id }
      )
      .then((r) => r.data),
}

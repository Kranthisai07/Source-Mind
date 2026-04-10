import apiClient from './client'
import type { Workspace, TeamMember } from '@/lib/types'

export const workspacesApi = {
  list: () =>
    apiClient.get<Workspace[]>('/v1/workspaces').then((r) => r.data),

  create: (body: { name: string; slug: string; visibility: string }) =>
    apiClient.post<Workspace>('/v1/workspaces', body).then((r) => r.data),

  getMembers: (workspaceId: string) =>
    apiClient.get<TeamMember[]>(`/v1/workspaces/${workspaceId}/members`).then((r) => r.data),
}

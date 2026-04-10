import apiClient from './client'
import type { UserProfile } from '@/lib/types'

export const teamApi = {
  me: () => apiClient.get<UserProfile>('/v1/team/me').then((r) => r.data),
  health: () => apiClient.get<{ status: string }>('/health').then((r) => r.data),
}

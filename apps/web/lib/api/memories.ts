import apiClient from './client'
import type { Memory, Job, Attribution, MemoryVersion, SearchFilters, SearchResponse } from '@/lib/types'

export interface IngestBody {
  content?: string
  url?: string
  workspace_id: string
  tags?: string[]
  source_type?: string
  idempotency_key?: string
}

export interface IngestResponse {
  job_id: string
  status: string
  estimated_ms?: number
}

export const memoriesApi = {
  ingest: ({ workspace_id, ...body }: IngestBody) =>
    apiClient
      .post<IngestResponse>('/v1/memories', body, { params: { workspace_id } })
      .then((r) => r.data),

  getJob: (jobId: string) =>
    apiClient.get<Job>(`/v1/memories/jobs/${jobId}`).then((r) => r.data),

  search: (query: string, filters: SearchFilters = {}) =>
    apiClient
      .post<SearchResponse>('/v1/memories/search', { query, ...filters })
      .then((r) => r.data),

  get: (id: string) =>
    apiClient.get<Memory>(`/v1/memories/${id}`).then((r) => r.data),

  update: (id: string, content: string) =>
    apiClient.patch<Memory>(`/v1/memories/${id}`, { content }).then((r) => r.data),

  delete: (id: string) =>
    apiClient.delete<{ deleted: boolean }>(`/v1/memories/${id}`).then((r) => r.data),

  getVersions: (id: string) =>
    apiClient.get<MemoryVersion[]>(`/v1/memories/${id}/versions`).then((r) => r.data),

  getAttribution: (id: string) =>
    apiClient.get<Attribution>(`/v1/memories/${id}/attribution`).then((r) => r.data),
}

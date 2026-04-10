'use client'

import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from '@tanstack/react-query'
import { memoriesApi, type IngestBody } from '@/lib/api/memories'
import type { SearchFilters } from '@/lib/types'
import toast from 'react-hot-toast'
import { errorMessage } from '@/lib/utils'

export const memoryKeys = {
  all: ['memories'] as const,
  lists: () => [...memoryKeys.all, 'list'] as const,
  list: (ws: string, filters?: unknown) => [...memoryKeys.lists(), ws, filters] as const,
  detail: (id: string) => [...memoryKeys.all, 'detail', id] as const,
  versions: (id: string) => [...memoryKeys.all, 'versions', id] as const,
  attribution: (id: string) => [...memoryKeys.all, 'attribution', id] as const,
  job: (jobId: string) => ['jobs', jobId] as const,
  search: (query: string, filters?: SearchFilters) => ['search', query, filters] as const,
}

export function useMemory(id: string) {
  return useQuery({
    queryKey: memoryKeys.detail(id),
    queryFn: () => memoriesApi.get(id),
    enabled: !!id,
    staleTime: 60_000,
  })
}

export function useMemoryVersions(id: string) {
  return useQuery({
    queryKey: memoryKeys.versions(id),
    queryFn: () => memoriesApi.getVersions(id),
    enabled: !!id,
  })
}

export function useMemoryAttribution(id: string) {
  return useQuery({
    queryKey: memoryKeys.attribution(id),
    queryFn: () => memoriesApi.getAttribution(id),
    enabled: !!id,
  })
}

export function useSearch(query: string, filters: SearchFilters = {}) {
  return useQuery({
    queryKey: memoryKeys.search(query, filters),
    queryFn: () => memoriesApi.search(query, filters),
    enabled: query.length > 2,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  })
}

export function useJobStatus(jobId: string | null) {
  return useQuery({
    queryKey: memoryKeys.job(jobId ?? ''),
    queryFn: () => memoriesApi.getJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status) return 1500
      return status === 'completed' || status === 'failed' ? false : 1500
    },
  })
}

export function useIngestMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: IngestBody) => memoriesApi.ingest(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: memoryKeys.all })
      qc.invalidateQueries({ queryKey: ['analytics'] })
    },
    onError: (err) => {
      toast.error(errorMessage(err))
    },
  })
}

export function useUpdateMemoryMutation(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (content: string) => memoriesApi.update(id, content),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: memoryKeys.detail(id) })
      qc.invalidateQueries({ queryKey: memoryKeys.versions(id) })
      qc.invalidateQueries({ queryKey: memoryKeys.attribution(id) })
      toast.success('Memory updated — attribution recomputed')
    },
    onError: (err) => {
      toast.error(errorMessage(err))
    },
  })
}

export function useDeleteMemoryMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => memoriesApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: memoryKeys.all })
      toast.success('Memory deleted')
    },
    onError: (err) => {
      toast.error(errorMessage(err))
    },
  })
}

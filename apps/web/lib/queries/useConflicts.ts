'use client'

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { conflictsApi, type ResolveBody } from '@/lib/api/conflicts'
import toast from 'react-hot-toast'
import { errorMessage } from '@/lib/utils'

export const conflictKeys = {
  all: ['conflicts'] as const,
  list: (workspaceId: string, status?: string) =>
    [...conflictKeys.all, 'list', workspaceId, status] as const,
  detail: (id: string) => [...conflictKeys.all, 'detail', id] as const,
}

export function useConflicts(workspaceId: string, status?: string) {
  return useQuery({
    queryKey: conflictKeys.list(workspaceId, status),
    queryFn: () => conflictsApi.list(workspaceId, { status }),
    enabled: !!workspaceId,
    staleTime: 30_000,
  })
}

export function useConflict(conflictId: string) {
  return useQuery({
    queryKey: conflictKeys.detail(conflictId),
    queryFn: () => conflictsApi.get(conflictId),
    enabled: !!conflictId,
  })
}

export function useReviewConflictMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (conflictId: string) => conflictsApi.review(conflictId),
    onSuccess: (_, conflictId) => {
      qc.invalidateQueries({ queryKey: conflictKeys.detail(conflictId) })
      qc.invalidateQueries({ queryKey: conflictKeys.all })
      toast.success('Conflict marked under review')
    },
    onError: (err) => toast.error(errorMessage(err)),
  })
}

export function useResolveConflictMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ conflictId, body }: { conflictId: string; body: ResolveBody }) =>
      conflictsApi.resolve(conflictId, body),
    onSuccess: (_, { conflictId }) => {
      qc.invalidateQueries({ queryKey: conflictKeys.detail(conflictId) })
      qc.invalidateQueries({ queryKey: conflictKeys.all })
      qc.invalidateQueries({ queryKey: ['analytics'] })
      toast.success('Conflict resolved')
    },
    onError: (err) => toast.error(errorMessage(err)),
  })
}

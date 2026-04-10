'use client'

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { workspacesApi } from '@/lib/api/workspaces'
import toast from 'react-hot-toast'
import { errorMessage } from '@/lib/utils'

export function useWorkspaces() {
  return useQuery({
    queryKey: ['workspaces'],
    queryFn: workspacesApi.list,
    staleTime: 120_000,
  })
}

export function useWorkspaceMembers(workspaceId: string) {
  return useQuery({
    queryKey: ['workspaces', workspaceId, 'members'],
    queryFn: () => workspacesApi.getMembers(workspaceId),
    enabled: !!workspaceId,
  })
}

export function useCreateWorkspaceMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: workspacesApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspaces'] })
      toast.success('Workspace created')
    },
    onError: (err) => toast.error(errorMessage(err)),
  })
}

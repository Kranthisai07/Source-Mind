'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { handoffApi } from '@/lib/api/handoff'
import toast from 'react-hot-toast'
import { errorMessage } from '@/lib/utils'

export function useInitiateHandoffMutation() {
  return useMutation({
    mutationFn: ({ workspaceId, departing_user_id }: { workspaceId: string; departing_user_id: string }) =>
      handoffApi.initiate(workspaceId, departing_user_id),
    onError: (err) => toast.error(errorMessage(err)),
  })
}

export function useAssignMemoryMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      workspaceId,
      memory_id,
      new_owner_id,
      handoff_record_id,
      note,
    }: {
      workspaceId: string
      memory_id: string
      new_owner_id: string
      handoff_record_id: string
      note?: string
    }) => handoffApi.assign(workspaceId, { memory_id, new_owner_id, handoff_record_id, note }),
    onSuccess: () => {
      toast.success('Memory assigned')
    },
    onError: (err) => toast.error(errorMessage(err)),
  })
}

export function useCompleteHandoffMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      workspaceId,
      departing_user_id,
      handoff_record_id,
    }: {
      workspaceId: string
      departing_user_id: string
      handoff_record_id: string
    }) => handoffApi.complete(workspaceId, departing_user_id, handoff_record_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['analytics'] })
      toast.success('Handoff completed — member marked as departed')
    },
    onError: (err) => toast.error(errorMessage(err)),
  })
}

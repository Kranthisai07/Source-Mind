'use client'

import { useEffect } from 'react'
import { useWorkspaces } from '@/lib/queries/useWorkspaces'
import { useAppStore } from '@/lib/store/useAppStore'
import { teamApi } from '@/lib/api/team'

/**
 * Auto-selects the first workspace on mount if none is active.
 * Also pings /health to set apiHealthy status.
 */
export function WorkspaceInit() {
  const { data: workspaces } = useWorkspaces()
  const { activeWorkspace, setActiveWorkspace, setApiHealthy } = useAppStore()

  useEffect(() => {
    if (!activeWorkspace && workspaces && workspaces.length > 0) {
      setActiveWorkspace(workspaces[0])
    }
  }, [workspaces, activeWorkspace, setActiveWorkspace])

  useEffect(() => {
    const checkHealth = async () => {
      try {
        await teamApi.health()
        setApiHealthy(true)
      } catch {
        setApiHealthy(false)
      }
    }
    checkHealth()
    const interval = setInterval(checkHealth, 30_000)
    return () => clearInterval(interval)
  }, [setApiHealthy])

  return null
}

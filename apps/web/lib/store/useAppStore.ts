import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Workspace } from '@/lib/types'

interface AppState {
  // Active workspace
  activeWorkspaceId: string | null
  activeWorkspace: Workspace | null
  setActiveWorkspace: (workspace: Workspace) => void

  // Sidebar
  sidebarCollapsed: boolean
  toggleSidebar: () => void

  // Modal state
  ingestModalOpen: boolean
  setIngestModalOpen: (open: boolean) => void

  // Active job being tracked
  activeJobId: string | null
  setActiveJobId: (id: string | null) => void

  // Search state
  searchQuery: string
  setSearchQuery: (q: string) => void

  // API health
  apiHealthy: boolean
  setApiHealthy: (healthy: boolean) => void
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      activeWorkspaceId: null,
      activeWorkspace: null,
      setActiveWorkspace: (workspace) =>
        set({ activeWorkspace: workspace, activeWorkspaceId: workspace.id }),

      sidebarCollapsed: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

      ingestModalOpen: false,
      setIngestModalOpen: (open) => set({ ingestModalOpen: open }),

      activeJobId: null,
      setActiveJobId: (id) => set({ activeJobId: id }),

      searchQuery: '',
      setSearchQuery: (q) => set({ searchQuery: q }),

      apiHealthy: true,
      setApiHealthy: (healthy) => set({ apiHealthy: healthy }),
    }),
    {
      name: 'sourcemind-app',
      partialize: (state) => ({
        activeWorkspaceId: state.activeWorkspaceId,
        activeWorkspace: state.activeWorkspace,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
    }
  )
)

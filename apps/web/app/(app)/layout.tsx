import { AuthInit } from '@/lib/AuthInit'
import { Sidebar } from '@/components/layout/Sidebar'
import { WorkspaceInit } from '@/components/workspace/WorkspaceInit'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <AuthInit />
      <div className="flex h-screen bg-bg overflow-hidden">
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <WorkspaceInit />
          {children}
        </div>
      </div>
    </>
  )
}

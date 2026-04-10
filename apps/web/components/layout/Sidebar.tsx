'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { UserButton } from '@clerk/nextjs'
import {
  LayoutDashboard,
  Brain,
  AlertTriangle,
  BarChart3,
  ArrowRightLeft,
  Settings,
  ChevronDown,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store/useAppStore'
import { useWorkspaces } from '@/lib/queries/useWorkspaces'
import { useConflicts } from '@/lib/queries/useConflicts'
import { motion, AnimatePresence } from 'framer-motion'
import { useState } from 'react'

const NAV_ITEMS = [
  { href: '/dashboard',  label: 'Dashboard',  icon: LayoutDashboard },
  { href: '/memories',   label: 'Memories',   icon: Brain },
  { href: '/conflicts',  label: 'Conflicts',  icon: AlertTriangle },
  { href: '/analytics',  label: 'Analytics',  icon: BarChart3 },
  { href: '/handoff',    label: 'Handoff',    icon: ArrowRightLeft },
  { href: '/settings',   label: 'Settings',   icon: Settings },
]

export function Sidebar() {
  const pathname = usePathname()
  const { activeWorkspace, setActiveWorkspace, apiHealthy } = useAppStore()
  const { data: workspaces } = useWorkspaces()
  const [wsOpen, setWsOpen] = useState(false)

  // Conflict badge
  const { data: conflictData } = useConflicts(activeWorkspace?.id ?? '', 'open')
  const openConflictCount = conflictData?.conflicts?.length ?? 0

  return (
    <aside className="w-60 flex-shrink-0 h-screen bg-surface border-r border-border flex flex-col overflow-hidden">
      {/* Wordmark */}
      <div className="px-5 py-5 border-b border-border">
        <h1 className="font-display text-2xl gradient-text leading-none">SourceMind</h1>
        <p className="text-muted text-xs mt-1 font-mono">Team Memory Platform</p>
      </div>

      {/* Workspace switcher */}
      <div className="px-3 py-3 border-b border-border">
        <button
          onClick={() => setWsOpen((v) => !v)}
          className="w-full flex items-center justify-between px-3 py-2 rounded-md hover:bg-elevated text-left transition-colors"
        >
          <div className="min-w-0">
            <p className="text-xs text-secondary font-mono uppercase tracking-wider mb-0.5">Workspace</p>
            <p className="text-sm text-primary font-medium truncate">
              {activeWorkspace?.name ?? 'Select workspace'}
            </p>
          </div>
          <ChevronDown
            className={cn('text-muted w-4 h-4 flex-shrink-0 transition-transform', wsOpen && 'rotate-180')}
          />
        </button>

        <AnimatePresence>
          {wsOpen && workspaces && (
            <motion.div
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.15 }}
              className="mt-1 bg-elevated border border-border rounded-md overflow-hidden"
            >
              {workspaces.map((ws) => (
                <button
                  key={ws.id}
                  onClick={() => { setActiveWorkspace(ws); setWsOpen(false) }}
                  className={cn(
                    'w-full text-left px-3 py-2 text-sm transition-colors',
                    activeWorkspace?.id === ws.id
                      ? 'text-accent-blue bg-[rgba(79,142,247,0.08)]'
                      : 'text-secondary hover:text-primary hover:bg-[rgba(255,255,255,0.03)]'
                  )}
                >
                  {ws.name}
                </button>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const isActive = pathname === href || pathname.startsWith(href + '/')
          const showBadge = href === '/conflicts' && openConflictCount > 0

          return (
            <Link
              key={href}
              href={href}
              className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-all duration-150',
                isActive
                  ? 'text-primary bg-elevated border-l-2 border-accent-blue pl-[10px]'
                  : 'text-secondary hover:text-primary hover:bg-elevated border-l-2 border-transparent'
              )}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              <span className="flex-1">{label}</span>
              {showBadge && (
                <span className="text-xs bg-accent-red text-white rounded-full px-1.5 py-0.5 font-mono leading-none">
                  {openConflictCount > 9 ? '9+' : openConflictCount}
                </span>
              )}
            </Link>
          )
        })}
      </nav>

      {/* Bottom: user + health */}
      <div className="px-3 py-4 border-t border-border space-y-3">
        <div className="flex items-center justify-between px-2">
          <div className="flex items-center gap-2">
            {apiHealthy ? (
              <Wifi className="w-3.5 h-3.5 text-accent-green" />
            ) : (
              <WifiOff className="w-3.5 h-3.5 text-accent-red" />
            )}
            <span className="text-xs font-mono text-muted">
              {apiHealthy ? 'API connected' : 'API unreachable'}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3 px-2">
          <UserButton
            appearance={{
              elements: {
                avatarBox: 'w-7 h-7',
              },
            }}
          />
          <span className="text-sm text-secondary truncate">Account</span>
        </div>
      </div>
    </aside>
  )
}

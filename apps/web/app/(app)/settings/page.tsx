'use client'

import { useState } from 'react'
import { Settings, Users, AlertTriangle, Shield } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { PageWrapper } from '@/components/layout/PageWrapper'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { SkeletonCard } from '@/components/ui/SkeletonCard'
import { ContributorAvatar } from '@/components/ui/ContributorAvatar'
import { useWorkspaceMembers } from '@/lib/queries/useWorkspaces'
import { useAppStore } from '@/lib/store/useAppStore'
import { formatDate, cn } from '@/lib/utils'
import type { TeamMember } from '@/lib/types'

const TABS = [
  { id: 'general', label: 'General', icon: Settings },
  { id: 'members', label: 'Members', icon: Users },
  { id: 'danger',  label: 'Danger Zone', icon: AlertTriangle },
]

const ROLE_COLORS: Record<TeamMember['role'], string> = {
  owner:  'purple',
  admin:  'blue',
  member: 'gray',
  viewer: 'gray',
}

const STATUS_COLORS: Record<TeamMember['status'], string> = {
  active:    '#10B981',
  departing: '#F59E0B',
  departed:  '#8888A0',
}

/* ── General tab ──────────────────────────────────────────────────── */
function GeneralTab() {
  const { activeWorkspace } = useAppStore()
  const [name, setName] = useState(activeWorkspace?.name ?? '')

  return (
    <div className="max-w-lg space-y-6">
      <div>
        <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
          Workspace name
        </label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full bg-bg border border-border rounded-lg px-3 py-2.5 text-sm text-primary focus:border-accent-blue focus:outline-none"
        />
      </div>
      <div>
        <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
          Workspace ID
        </label>
        <p className="text-sm font-mono text-muted">{activeWorkspace?.id ?? '—'}</p>
      </div>
      <div>
        <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
          Slug
        </label>
        <p className="text-sm font-mono text-muted">{activeWorkspace?.slug ?? '—'}</p>
      </div>
      <Button size="sm" disabled={name === activeWorkspace?.name}>
        Save changes
      </Button>
    </div>
  )
}

/* ── Members tab ──────────────────────────────────────────────────── */
function MembersTab() {
  const { activeWorkspace } = useAppStore()
  const { data: members, isLoading } = useWorkspaceMembers(activeWorkspace?.id ?? '')

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-secondary">
          {members ? `${members.length} member${members.length !== 1 ? 's' : ''}` : ''}
        </p>
        <Button size="sm" variant="secondary">
          <Users className="w-3.5 h-3.5" /> Invite member
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : !members?.length ? (
        <p className="text-muted text-sm py-8 text-center">No members yet.</p>
      ) : (
        <div className="bg-surface border border-border rounded-xl overflow-hidden">
          {members.map((m, i) => (
            <div
              key={m.id}
              className={cn(
                'flex items-center gap-4 px-5 py-4',
                i < members.length - 1 && 'border-b border-border'
              )}
            >
              <ContributorAvatar name={m.name} size="md" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-primary truncate">{m.name}</p>
                <p className="text-xs text-muted font-mono">{m.email}</p>
              </div>
              <div className="flex items-center gap-3">
                {/* Status dot */}
                <span className="flex items-center gap-1.5 text-xs">
                  <span
                    className="w-1.5 h-1.5 rounded-full"
                    style={{ backgroundColor: STATUS_COLORS[m.status] }}
                  />
                  <span className="text-muted">{m.status}</span>
                </span>
                {/* Role badge */}
                <Badge variant={ROLE_COLORS[m.role] as 'purple' | 'blue' | 'gray'}>
                  {m.role}
                </Badge>
                {/* Joined date */}
                <span className="text-xs text-muted font-mono hidden lg:block">
                  joined {formatDate(m.joined_at)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Danger zone tab ──────────────────────────────────────────────── */
function DangerTab() {
  return (
    <div className="space-y-4 max-w-lg">
      <div className="p-5 border border-[rgba(239,68,68,0.25)] rounded-xl bg-[rgba(239,68,68,0.04)] space-y-3">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-[#EF4444]" />
          <h4 className="text-sm font-medium text-primary">Delete workspace</h4>
        </div>
        <p className="text-sm text-secondary">
          Permanently delete this workspace and all its memories, contributors, and history.
          This action cannot be undone.
        </p>
        <Button variant="danger" size="sm">
          Delete workspace
        </Button>
      </div>

      <div className="p-5 border border-[rgba(245,158,11,0.25)] rounded-xl bg-[rgba(245,158,11,0.04)] space-y-3">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-[#F59E0B]" />
          <h4 className="text-sm font-medium text-primary">Export workspace data</h4>
        </div>
        <p className="text-sm text-secondary">
          Download a full export of all memories, attribution records, and conflict history.
        </p>
        <Button variant="secondary" size="sm">
          Request export
        </Button>
      </div>
    </div>
  )
}

/* ── Main page ────────────────────────────────────────────────────── */
export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<'general' | 'members' | 'danger'>('general')

  return (
    <PageWrapper>
      <TopBar title="Settings" />

      <div className="px-8 py-6 space-y-6">
        {/* Tab bar */}
        <div className="flex gap-1 border-b border-border">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id as typeof activeTab)}
              className={cn(
                'flex items-center gap-2 px-4 py-2.5 text-sm transition-colors border-b-2 -mb-px',
                activeTab === id
                  ? 'text-primary border-accent-blue'
                  : 'text-secondary border-transparent hover:text-primary'
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {activeTab === 'general' && <GeneralTab />}
        {activeTab === 'members' && <MembersTab />}
        {activeTab === 'danger'  && <DangerTab />}
      </div>
    </PageWrapper>
  )
}

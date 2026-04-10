'use client'

import { useState } from 'react'
import { UserX, ChevronRight, ChevronLeft, CheckCircle, AlertTriangle, User } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { PageWrapper } from '@/components/layout/PageWrapper'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { ContributorAvatar } from '@/components/ui/ContributorAvatar'
import {
  useInitiateHandoffMutation,
  useAssignMemoryMutation,
  useCompleteHandoffMutation,
} from '@/lib/queries/useHandoff'
import { useAppStore } from '@/lib/store/useAppStore'
import { truncate, formatPct } from '@/lib/utils'
import type { HandoffSummary, HandoffMemoryItem } from '@/lib/types'

/* ── Wizard steps ─────────────────────────────────────────────────── */
const STEPS = [
  { id: 1, label: 'Identify member' },
  { id: 2, label: 'Review memories' },
  { id: 3, label: 'Assign & complete' },
]

/* ── Step 1: User ID form ─────────────────────────────────────────── */
function StepIdentify({
  onNext,
  loading,
}: {
  onNext: (userId: string) => void
  loading: boolean
}) {
  const [userId, setUserId] = useState('')
  return (
    <div className="max-w-md space-y-5">
      <div>
        <h3 className="text-base font-medium text-primary mb-1">Departing team member</h3>
        <p className="text-sm text-secondary">
          Enter the user ID of the member leaving the team. Their critical memories will be
          classified into tiers for handoff.
        </p>
      </div>
      <div>
        <label className="text-xs text-secondary font-mono uppercase tracking-wider mb-1.5 block">
          User ID
        </label>
        <input
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          placeholder="usr_..."
          className="w-full bg-bg border border-border rounded-lg px-3 py-2.5 text-sm text-primary placeholder-muted focus:border-accent-blue focus:outline-none"
        />
      </div>
      <Button onClick={() => onNext(userId)} loading={loading} disabled={!userId.trim()}>
        Classify memories <ChevronRight className="w-3.5 h-3.5" />
      </Button>
    </div>
  )
}

/* ── Memory tier card ─────────────────────────────────────────────── */
function MemoryTierCard({
  item,
  handoffRecordId,
  workspaceId,
  assigned,
  onAssigned,
}: {
  item: HandoffMemoryItem
  handoffRecordId: string
  workspaceId: string
  assigned: boolean
  onAssigned: (memoryId: string) => void
}) {
  const [newOwnerId, setNewOwnerId] = useState(item.suggested_successor_id ?? '')
  const assignMutation = useAssignMemoryMutation()

  async function handleAssign() {
    if (!newOwnerId) return
    await assignMutation.mutateAsync({ workspaceId, memory_id: item.memory_id, new_owner_id: newOwnerId, handoff_record_id: handoffRecordId })
    onAssigned(item.memory_id)
  }

  return (
    <div className={`bg-surface border rounded-xl p-4 transition-colors ${assigned ? 'border-[rgba(16,185,129,0.3)] bg-[rgba(16,185,129,0.03)]' : 'border-border'}`}>
      <div className="flex items-start justify-between gap-4 mb-3">
        <p className="text-sm text-primary leading-relaxed">{truncate(item.content, 160)}</p>
        {assigned && <CheckCircle className="w-4 h-4 text-accent-green flex-shrink-0 mt-0.5" />}
      </div>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs font-mono text-muted">
          importance: {(item.importance_score * 100).toFixed(0)}%
        </span>
        {item.suggested_successor_name && (
          <>
            <span className="text-muted">·</span>
            <span className="text-xs text-secondary">
              Suggested: <span className="text-accent-blue">{item.suggested_successor_name}</span>
              {item.successor_confidence && (
                <span className="text-muted"> ({(item.successor_confidence * 100).toFixed(0)}% confident)</span>
              )}
            </span>
          </>
        )}
      </div>
      {!assigned && (
        <div className="flex items-center gap-2">
          <input
            value={newOwnerId}
            onChange={(e) => setNewOwnerId(e.target.value)}
            placeholder="New owner user ID..."
            className="flex-1 bg-bg border border-border rounded-lg px-3 py-1.5 text-xs text-primary placeholder-muted focus:border-accent-blue focus:outline-none"
          />
          <Button
            size="sm"
            onClick={handleAssign}
            loading={assignMutation.isPending}
            disabled={!newOwnerId}
          >
            Assign
          </Button>
        </div>
      )}
    </div>
  )
}

/* ── Step 2: Review tier summary ──────────────────────────────────── */
function StepReview({
  summary,
  onBack,
  onNext,
}: {
  summary: HandoffSummary
  onBack: () => void
  onNext: () => void
}) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-surface border border-[rgba(239,68,68,0.3)] rounded-xl p-4">
          <p className="text-xs font-mono text-muted uppercase tracking-wider mb-1">Tier 1 — Critical</p>
          <p className="text-2xl font-display font-medium text-primary">{summary.tier_1_critical.length}</p>
          <p className="text-xs text-secondary mt-1">importance &gt; 0.8, sole contributor</p>
        </div>
        <div className="bg-surface border border-[rgba(245,158,11,0.3)] rounded-xl p-4">
          <p className="text-xs font-mono text-muted uppercase tracking-wider mb-1">Tier 2 — Important</p>
          <p className="text-2xl font-display font-medium text-primary">{summary.tier_2_important.length}</p>
          <p className="text-xs text-secondary mt-1">importance &gt; 0.5</p>
        </div>
        <div className="bg-surface border border-border rounded-xl p-4">
          <p className="text-xs font-mono text-muted uppercase tracking-wider mb-1">Tier 3 — Standard</p>
          <p className="text-2xl font-display font-medium text-primary">{summary.tier_3_standard_count}</p>
          <p className="text-xs text-secondary mt-1">auto-redistributed</p>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ChevronLeft className="w-3.5 h-3.5" /> Back
        </Button>
        <Button onClick={onNext}>
          Assign critical memories <ChevronRight className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  )
}

/* ── Step 3: Assign + complete ────────────────────────────────────── */
function StepAssign({
  summary,
  workspaceId,
  departingUserId,
  onBack,
  onDone,
}: {
  summary: HandoffSummary
  workspaceId: string
  departingUserId: string
  onBack: () => void
  onDone: () => void
}) {
  const [assigned, setAssigned] = useState<Set<string>>(new Set())
  const completeMutation = useCompleteHandoffMutation()

  const tier1 = summary.tier_1_critical
  const tier2 = summary.tier_2_important
  const allCriticalAssigned = tier1.every((m) => assigned.has(m.memory_id))

  async function handleComplete() {
    await completeMutation.mutateAsync({
      workspaceId,
      departing_user_id: departingUserId,
      handoff_record_id: summary.handoff_record_id,
    })
    onDone()
  }

  return (
    <div className="space-y-6">
      {/* Tier 1 */}
      {tier1.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle className="w-4 h-4 text-[#EF4444]" />
            <h4 className="text-sm font-medium text-primary">Tier 1 — Critical</h4>
            <Badge variant="gray" className="ml-auto">{assigned.size}/{tier1.length} assigned</Badge>
          </div>
          <div className="space-y-3">
            {tier1.map((item) => (
              <MemoryTierCard
                key={item.memory_id}
                item={item}
                handoffRecordId={summary.handoff_record_id}
                workspaceId={workspaceId}
                assigned={assigned.has(item.memory_id)}
                onAssigned={(id) => setAssigned((prev) => new Set([...prev, id]))}
              />
            ))}
          </div>
        </div>
      )}

      {/* Tier 2 */}
      {tier2.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <h4 className="text-sm font-medium text-primary">Tier 2 — Important</h4>
          </div>
          <div className="space-y-3">
            {tier2.map((item) => (
              <MemoryTierCard
                key={item.memory_id}
                item={item}
                handoffRecordId={summary.handoff_record_id}
                workspaceId={workspaceId}
                assigned={assigned.has(item.memory_id)}
                onAssigned={(id) => setAssigned((prev) => new Set([...prev, id]))}
              />
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center justify-between pt-2">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ChevronLeft className="w-3.5 h-3.5" /> Back
        </Button>
        <Button
          onClick={handleComplete}
          loading={completeMutation.isPending}
          disabled={!allCriticalAssigned && tier1.length > 0}
        >
          Complete handoff
        </Button>
      </div>
      {tier1.length > 0 && !allCriticalAssigned && (
        <p className="text-xs text-muted text-right">
          Assign all Tier 1 memories before completing
        </p>
      )}
    </div>
  )
}

/* ── Main page ────────────────────────────────────────────────────── */
export default function HandoffPage() {
  const { activeWorkspace } = useAppStore()
  const wsId = activeWorkspace?.id ?? ''

  const [step, setStep] = useState(1)
  const [departingUserId, setDepartingUserId] = useState('')
  const [summary, setSummary] = useState<HandoffSummary | null>(null)
  const [done, setDone] = useState(false)

  const initiateMutation = useInitiateHandoffMutation()

  async function handleIdentify(userId: string) {
    setDepartingUserId(userId)
    const result = await initiateMutation.mutateAsync({ workspaceId: wsId, departing_user_id: userId })
    setSummary(result)
    setStep(2)
  }

  if (!wsId) {
    return (
      <PageWrapper>
        <TopBar title="Handoff" />
        <div className="px-8 py-6">
          <EmptyState icon={UserX} title="No workspace selected" description="Select a workspace to initiate a handoff." />
        </div>
      </PageWrapper>
    )
  }

  return (
    <PageWrapper>
      <TopBar title="Knowledge Handoff" subtitle="Transfer a departing member's critical memories" />

      <div className="px-8 py-6 space-y-8">
        {/* Step indicator */}
        <div className="flex items-center gap-3">
          {STEPS.map((s, i) => (
            <div key={s.id} className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <div
                  className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-mono font-medium transition-colors ${
                    step > s.id
                      ? 'bg-accent-green text-bg'
                      : step === s.id
                      ? 'bg-accent-blue text-bg'
                      : 'bg-elevated text-muted border border-border'
                  }`}
                >
                  {step > s.id ? <CheckCircle className="w-3.5 h-3.5" /> : s.id}
                </div>
                <span className={`text-sm ${step === s.id ? 'text-primary font-medium' : 'text-muted'}`}>
                  {s.label}
                </span>
              </div>
              {i < STEPS.length - 1 && <div className="w-12 h-px bg-border" />}
            </div>
          ))}
        </div>

        {/* Step content */}
        <div className="bg-surface border border-border rounded-xl p-6">
          {done ? (
            <div className="text-center py-8 space-y-3">
              <CheckCircle className="w-10 h-10 text-accent-green mx-auto" />
              <h3 className="text-lg font-medium text-primary">Handoff complete</h3>
              <p className="text-sm text-secondary">
                The member has been marked as departed and their memories have been reassigned.
              </p>
              <Button variant="secondary" onClick={() => { setStep(1); setDone(false); setSummary(null); setDepartingUserId('') }}>
                Start another handoff
              </Button>
            </div>
          ) : step === 1 ? (
            <StepIdentify onNext={handleIdentify} loading={initiateMutation.isPending} />
          ) : step === 2 && summary ? (
            <StepReview summary={summary} onBack={() => setStep(1)} onNext={() => setStep(3)} />
          ) : step === 3 && summary ? (
            <StepAssign
              summary={summary}
              workspaceId={wsId}
              departingUserId={departingUserId}
              onBack={() => setStep(2)}
              onDone={() => setDone(true)}
            />
          ) : null}
        </div>
      </div>
    </PageWrapper>
  )
}

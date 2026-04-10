'use client'

import { useEffect } from 'react'
import {
  Clock, FileText, Scissors, Sparkles, Cpu, Users, Database, CheckCircle, XCircle
} from 'lucide-react'
import { useJobStatus } from '@/lib/queries/useMemories'
import { type JobStatus } from '@/lib/types'
import { cn, jobStatusLabel } from '@/lib/utils'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import Link from 'next/link'

const STAGES: { status: JobStatus; icon: React.ElementType; label: string }[] = [
  { status: 'queued',           icon: Clock,        label: 'Queued' },
  { status: 'extracting',       icon: FileText,     label: 'Extracting content' },
  { status: 'chunking',         icon: Scissors,     label: 'Chunking text' },
  { status: 'extracting_facts', icon: Sparkles,     label: 'Extracting facts' },
  { status: 'embedding',        icon: Cpu,          label: 'Generating embeddings' },
  { status: 'attributing',      icon: Users,        label: 'Computing attribution' },
  { status: 'indexing',         icon: Database,     label: 'Indexing memories' },
  { status: 'completed',        icon: CheckCircle,  label: 'Complete' },
]

const ORDER = STAGES.map((s) => s.status)

function stageIndex(status: JobStatus): number {
  return ORDER.indexOf(status)
}

interface JobStatusTrackerProps {
  jobId: string
  open: boolean
  onClose: () => void
}

export function JobStatusTracker({ jobId, open, onClose }: JobStatusTrackerProps) {
  const { data: job } = useJobStatus(open ? jobId : null)

  const currentIdx = job ? stageIndex(job.status) : 0
  const isFailed = job?.status === 'failed'
  const isDone = job?.status === 'completed'

  return (
    <Modal
      open={open}
      onOpenChange={(v) => { if (!v) onClose() }}
      title="Processing document"
      description="Your content is being analysed and indexed"
      size="sm"
    >
      <div className="p-6 space-y-4">
        <div className="space-y-1">
          {STAGES.slice(0, 8).map(({ status, icon: Icon, label }, i) => {
            const isCompleted = i < currentIdx
            const isActive = i === currentIdx && !isDone && !isFailed
            const isCurrent = isDone && status === 'completed'
            const isFail = isFailed && status === job?.status

            return (
              <div key={status} className="flex items-center gap-3 py-1.5 px-2 rounded-md">
                {/* Dot / Icon */}
                <div className="w-5 h-5 flex items-center justify-center flex-shrink-0">
                  {isCompleted || isCurrent ? (
                    <CheckCircle className="w-4 h-4 text-accent-green" />
                  ) : isFail ? (
                    <XCircle className="w-4 h-4 text-accent-red" />
                  ) : isActive ? (
                    <div className="w-2.5 h-2.5 rounded-full bg-accent-blue pulse-dot" />
                  ) : (
                    <div className="w-2.5 h-2.5 rounded-full bg-border" />
                  )}
                </div>

                {/* Label */}
                <span
                  className={cn(
                    'text-sm transition-colors',
                    isCompleted || isCurrent ? 'text-accent-green' :
                    isFail ? 'text-accent-red' :
                    isActive ? 'text-accent-blue font-medium' :
                    'text-muted'
                  )}
                >
                  {label}
                </span>

                {/* Spinner for active */}
                {isActive && (
                  <svg className="animate-spin w-3.5 h-3.5 text-accent-blue ml-auto" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                )}
              </div>
            )
          })}
        </div>

        {/* Result */}
        {isDone && job.memories_created !== undefined && (
          <div className="mt-4 p-4 bg-[rgba(16,185,129,0.08)] border border-[rgba(16,185,129,0.2)] rounded-lg text-center">
            <p className="text-accent-green font-medium">
              ✓ {job.memories_created} {job.memories_created === 1 ? 'memory' : 'memories'} created
            </p>
            {job.processing_time_ms && (
              <p className="text-xs text-muted font-mono mt-1">{job.processing_time_ms}ms</p>
            )}
          </div>
        )}

        {isFailed && (
          <div className="mt-4 p-4 bg-[rgba(239,68,68,0.08)] border border-[rgba(239,68,68,0.2)] rounded-lg">
            <p className="text-accent-red text-sm">{job?.error ?? 'Processing failed'}</p>
          </div>
        )}

        {(isDone || isFailed) && (
          <Button variant="secondary" className="w-full" onClick={onClose}>
            Close
          </Button>
        )}
      </div>
    </Modal>
  )
}

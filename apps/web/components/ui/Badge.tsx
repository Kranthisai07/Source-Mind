import { cn } from '@/lib/utils'

type BadgeVariant = 'blue' | 'purple' | 'green' | 'amber' | 'red' | 'gray' | 'cyan'

const variantStyles: Record<BadgeVariant, string> = {
  blue:   'bg-[rgba(79,142,247,0.12)] text-accent-blue border-[rgba(79,142,247,0.2)]',
  purple: 'bg-[rgba(139,92,246,0.12)] text-accent-purple border-[rgba(139,92,246,0.2)]',
  green:  'bg-[rgba(16,185,129,0.12)] text-accent-green border-[rgba(16,185,129,0.2)]',
  amber:  'bg-[rgba(245,158,11,0.12)] text-accent-amber border-[rgba(245,158,11,0.2)]',
  red:    'bg-[rgba(239,68,68,0.12)] text-accent-red border-[rgba(239,68,68,0.2)]',
  gray:   'bg-[rgba(136,136,160,0.12)] text-secondary border-[rgba(136,136,160,0.2)]',
  cyan:   'bg-[rgba(6,182,212,0.12)] text-accent-cyan border-[rgba(6,182,212,0.2)]',
}

interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  className?: string
}

export function Badge({ variant = 'gray', children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center text-xs font-mono px-2 py-0.5 rounded border leading-none',
        variantStyles[variant],
        className
      )}
    >
      {children}
    </span>
  )
}

export function ConflictStatusBadge({ status }: { status: string }) {
  const v: BadgeVariant =
    status === 'open' ? 'red' :
    status === 'under_review' ? 'amber' :
    status === 'resolved' ? 'green' : 'gray'
  const label =
    status === 'open' ? 'OPEN' :
    status === 'under_review' ? 'UNDER REVIEW' :
    status === 'resolved' ? 'RESOLVED' : 'DEFERRED'
  return <Badge variant={v}>{label}</Badge>
}

export function TierBadge({ tier }: { tier: 1 | 2 | 3 }) {
  const v: BadgeVariant = tier === 1 ? 'red' : tier === 2 ? 'amber' : 'gray'
  const label = tier === 1 ? 'TIER 1 · CRITICAL' : tier === 2 ? 'TIER 2 · IMPORTANT' : 'TIER 3 · STANDARD'
  return <Badge variant={v}>{label}</Badge>
}

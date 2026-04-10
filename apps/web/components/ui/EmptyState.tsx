import { type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}

export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center py-20 text-center', className)}>
      <div className="w-14 h-14 rounded-xl bg-elevated border border-border flex items-center justify-center mb-4">
        <Icon className="w-6 h-6 text-muted" />
      </div>
      <h3 className="text-primary font-medium mb-1">{title}</h3>
      {description && <p className="text-secondary text-sm max-w-sm text-balance">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

import { type ActivityEvent } from '@/lib/types'
import { formatRelativeTime } from '@/lib/utils'
import { ContributorAvatar } from '@/components/ui/ContributorAvatar'

const eventDot: Record<string, string> = {
  'memory.created':     '#4F8EF7',
  'memory.updated':     '#8B5CF6',
  'conflict.detected':  '#F59E0B',
  'handoff.initiated':  '#EF4444',
}

function dotColor(type: string): string {
  return eventDot[type] ?? '#4A4A5A'
}

interface ActivityFeedProps {
  events: ActivityEvent[]
}

export function ActivityFeed({ events }: ActivityFeedProps) {
  if (events.length === 0) {
    return <p className="text-muted text-sm py-4 text-center">No recent activity</p>
  }

  return (
    <div className="space-y-1">
      {events.slice(0, 10).map((event, i) => (
        <div key={i} className="flex items-start gap-3 py-2 px-3 rounded-md hover:bg-elevated/50 transition-colors">
          <div className="flex-shrink-0 mt-1.5">
            <div
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: dotColor(event.event_type) }}
            />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-secondary line-clamp-1">{event.description}</p>
            {event.user_name && (
              <p className="text-xs text-muted mt-0.5">{event.user_name}</p>
            )}
          </div>
          <span className="text-xs text-muted flex-shrink-0 font-mono">
            {formatRelativeTime(event.created_at)}
          </span>
        </div>
      ))}
    </div>
  )
}

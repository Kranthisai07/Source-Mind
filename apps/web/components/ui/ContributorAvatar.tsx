import { cn, initials, hashColor } from '@/lib/utils'

interface ContributorAvatarProps {
  name: string
  size?: 'xs' | 'sm' | 'md' | 'lg'
  contributionPct?: number
  className?: string
}

const sizeMap = {
  xs: { outer: 'w-5 h-5', text: 'text-[9px]' },
  sm: { outer: 'w-7 h-7', text: 'text-xs' },
  md: { outer: 'w-9 h-9', text: 'text-sm' },
  lg: { outer: 'w-11 h-11', text: 'text-base' },
}

export function ContributorAvatar({ name, size = 'sm', className }: ContributorAvatarProps) {
  const color = hashColor(name)
  const { outer, text } = sizeMap[size]

  return (
    <div
      className={cn('rounded-full flex items-center justify-center font-mono font-medium flex-shrink-0', outer, className)}
      style={{ backgroundColor: color + '22', color, border: `1px solid ${color}44` }}
      title={name}
    >
      <span className={text}>{initials(name)}</span>
    </div>
  )
}

export function ContributorAvatarStack({ names, max = 3 }: { names: string[]; max?: number }) {
  const shown = names.slice(0, max)
  const overflow = names.length - max

  return (
    <div className="flex items-center">
      {shown.map((name, i) => (
        <div key={name} style={{ marginLeft: i > 0 ? '-6px' : 0, zIndex: shown.length - i }}>
          <ContributorAvatar name={name} size="xs" />
        </div>
      ))}
      {overflow > 0 && (
        <div
          className="w-5 h-5 rounded-full bg-elevated border border-border text-muted text-[9px] font-mono flex items-center justify-center"
          style={{ marginLeft: '-6px' }}
        >
          +{overflow}
        </div>
      )}
    </div>
  )
}

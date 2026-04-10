'use client'

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import { CONTRIB_COLORS } from '@/lib/utils'
import type { TopContributor } from '@/lib/types'

interface ContributionChartProps {
  contributors: TopContributor[]
}

// Build synthetic weekly buckets from contributor totals for visualization
function buildChartData(contributors: TopContributor[]) {
  const weeks = ['6w ago', '5w ago', '4w ago', '3w ago', '2w ago', 'Last week', 'This week']
  return weeks.map((week, i) => {
    const entry: Record<string, number | string> = { week }
    contributors.forEach((c) => {
      // Distribute count across weeks with slight ramp-up toward recent
      const weight = (i + 1) / 28 // normalized 0..1
      entry[c.name] = Math.round((c.count ?? 0) * weight)
    })
    return entry
  })
}

interface CustomTooltipProps {
  active?: boolean
  payload?: { name: string; value: number; color: string }[]
  label?: string
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-elevated border border-border rounded-lg px-4 py-3 shadow-xl text-xs space-y-1">
      <p className="text-secondary font-mono mb-2">{label}</p>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: p.color }} />
          <span className="text-secondary">{p.name}</span>
          <span className="text-primary font-mono ml-auto pl-4">{p.value}</span>
        </div>
      ))}
    </div>
  )
}

export function ContributionChart({ contributors }: ContributionChartProps) {
  if (!contributors.length) return null

  const top5 = contributors.slice(0, 5)
  const data = buildChartData(top5)

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}>
        <defs>
          {top5.map((c, i) => (
            <linearGradient key={c.user_id} id={`grad-${i}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={CONTRIB_COLORS[i]} stopOpacity={0.25} />
              <stop offset="95%" stopColor={CONTRIB_COLORS[i]} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
        <XAxis
          dataKey="week"
          tick={{ fill: '#666680', fontSize: 10, fontFamily: 'var(--font-mono)' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: '#666680', fontSize: 10, fontFamily: 'var(--font-mono)' }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip content={<CustomTooltip />} />
        {top5.map((c, i) => (
          <Area
            key={c.user_id}
            type="monotone"
            dataKey={c.name}
            stroke={CONTRIB_COLORS[i]}
            strokeWidth={1.5}
            fill={`url(#grad-${i})`}
            dot={false}
            activeDot={{ r: 4, strokeWidth: 0 }}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}

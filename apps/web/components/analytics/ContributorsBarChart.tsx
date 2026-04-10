'use client'

import { BarChart, Bar, XAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { CONTRIB_COLORS } from '@/lib/utils'

interface Props {
  data: { name: string; count: number; color: string }[]
}

export default function ContributorsBarChart({ data }: Props) {
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} layout="vertical" margin={{ left: 0, right: 16 }}>
        <XAxis type="number" hide />
        <Tooltip
          cursor={{ fill: 'rgba(255,255,255,0.03)' }}
          contentStyle={{
            background: '#1A1A24',
            border: '1px solid #2A2A3A',
            borderRadius: 6,
            fontSize: 12,
            color: '#E8E8F0',
          }}
          formatter={(v: number) => [`${v} memories`, '']}
        />
        <Bar dataKey="count" radius={[0, 4, 4, 0]} maxBarSize={28}>
          {data.map((entry, i) => (
            <Cell key={i} fill={entry.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

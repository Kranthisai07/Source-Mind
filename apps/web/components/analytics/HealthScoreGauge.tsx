'use client'

import { useEffect, useRef } from 'react'
import { healthColor, formatScore } from '@/lib/utils'

interface HealthScoreGaugeProps {
  score: number
  size?: number
  breakdown?: {
    coverage: number
    freshness: number
    conflict: number
    attribution: number
  }
}

export function HealthScoreGauge({ score, size = 200, breakdown }: HealthScoreGaugeProps) {
  const pathRef = useRef<SVGCircleElement>(null)
  const color = healthColor(score)
  const cx = size / 2
  const cy = size / 2
  const radius = size / 2 - 16
  const circumference = 2 * Math.PI * radius
  // Draw 270° arc (from 225° to 135°, leaving a 90° gap at bottom)
  const arcLength = circumference * 0.75
  const offset = arcLength - arcLength * Math.max(0, Math.min(1, score))

  useEffect(() => {
    if (!pathRef.current) return
    pathRef.current.style.setProperty('--arc-length', String(arcLength))
    pathRef.current.style.setProperty('--arc-offset', String(offset))
  }, [arcLength, offset])

  const subMetrics = breakdown
    ? [
        { label: 'Coverage',    value: breakdown.coverage },
        { label: 'Freshness',   value: breakdown.freshness },
        { label: 'Conflicts',   value: breakdown.conflict },
        { label: 'Attribution', value: breakdown.attribution },
      ]
    : []

  return (
    <div className="flex flex-col items-center gap-6">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
          {/* Track */}
          <circle
            cx={cx} cy={cy} r={radius}
            fill="none"
            stroke="#2A2A3A"
            strokeWidth={10}
            strokeDasharray={`${arcLength} ${circumference}`}
            strokeDashoffset={0}
            strokeLinecap="round"
            transform={`rotate(135 ${cx} ${cy})`}
          />
          {/* Filled arc */}
          <circle
            ref={pathRef}
            cx={cx} cy={cy} r={radius}
            fill="none"
            stroke={color}
            strokeWidth={10}
            strokeDasharray={`${arcLength} ${circumference}`}
            strokeDashoffset={offset}
            strokeLinecap="round"
            transform={`rotate(135 ${cx} ${cy})`}
            style={{ transition: 'stroke-dashoffset 800ms ease-out, stroke 300ms' }}
          />
        </svg>
        {/* Center text */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-display text-4xl font-medium" style={{ color }}>
            {formatScore(score)}
          </span>
          <span className="text-muted text-xs font-mono mt-1">health score</span>
        </div>
      </div>

      {breakdown && (
        <div className="w-full grid grid-cols-2 gap-x-6 gap-y-3">
          {subMetrics.map(({ label, value }) => (
            <div key={label}>
              <div className="flex justify-between items-center mb-1">
                <span className="text-xs text-secondary">{label}</span>
                <span className="text-xs font-mono text-muted">{formatScore(value)}</span>
              </div>
              <div className="h-1 bg-border rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{ width: `${value * 100}%`, backgroundColor: healthColor(value) }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

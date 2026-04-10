import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { formatDistanceToNow, format } from 'date-fns'
import { ApiError } from './types'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatRelativeTime(date: string | Date): string {
  return formatDistanceToNow(new Date(date), { addSuffix: true })
}

export function formatDate(date: string | Date): string {
  return format(new Date(date), 'MMM d, yyyy')
}

export function formatDateTime(date: string | Date): string {
  return format(new Date(date), 'MMM d, yyyy · h:mm a')
}

export function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str
  return str.slice(0, maxLen).trimEnd() + '…'
}

export function formatPct(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

export function formatScore(value: number): string {
  return value.toFixed(2)
}

// Map contributor index → CSS custom property color
export function contribColor(index: number): string {
  return `var(--contrib-${index % 8})`
}

// Hard colors array for recharts (doesn't support CSS vars directly)
export const CONTRIB_COLORS = [
  '#4F8EF7',
  '#8B5CF6',
  '#10B981',
  '#F59E0B',
  '#EF4444',
  '#06B6D4',
  '#EC4899',
  '#84CC16',
]

export function riskColor(risk: 'HIGH' | 'MEDIUM' | 'LOW'): string {
  switch (risk) {
    case 'HIGH':   return '#EF4444'
    case 'MEDIUM': return '#F59E0B'
    case 'LOW':    return '#10B981'
  }
}

export function riskBg(risk: 'HIGH' | 'MEDIUM' | 'LOW'): string {
  switch (risk) {
    case 'HIGH':   return 'rgba(239,68,68,0.12)'
    case 'MEDIUM': return 'rgba(245,158,11,0.12)'
    case 'LOW':    return 'rgba(16,185,129,0.12)'
  }
}

export function healthColor(score: number): string {
  if (score >= 0.8) return '#10B981'
  if (score >= 0.5) return '#F59E0B'
  return '#EF4444'
}

export function conflictStatusColor(status: string): string {
  switch (status) {
    case 'open':         return '#EF4444'
    case 'under_review': return '#F59E0B'
    case 'resolved':     return '#10B981'
    case 'deferred':     return '#8888A0'
    default:             return '#8888A0'
  }
}

export function conflictStatusLabel(status: string): string {
  switch (status) {
    case 'open':         return 'Open'
    case 'under_review': return 'Under Review'
    case 'resolved':     return 'Resolved'
    case 'deferred':     return 'Deferred'
    default:             return status
  }
}

export function jobStatusLabel(status: string): string {
  switch (status) {
    case 'queued':           return 'Queued'
    case 'extracting':       return 'Extracting content'
    case 'chunking':         return 'Chunking text'
    case 'extracting_facts': return 'Extracting facts'
    case 'embedding':        return 'Generating embeddings'
    case 'attributing':      return 'Computing attribution'
    case 'indexing':         return 'Indexing memories'
    case 'completed':        return 'Complete'
    case 'failed':           return 'Failed'
    default:                 return status
  }
}

const SM_ERRORS: Record<string, string> = {
  SM010: 'Missing content or URL',
  SM011: 'URL could not be reached',
  SM012: 'No access to this workspace',
  SM013: 'Content too large (max 500K characters)',
  SM040: 'Conflict not found',
  SM050: 'Handoff record not found',
  NETWORK_ERROR: 'Connection failed — check your API server',
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return SM_ERRORS[err.code] ?? err.message
  }
  if (err instanceof Error) return err.message
  return 'An unexpected error occurred'
}

// Generate initials from name
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('')
}

// Stable color hash for contributor avatars
export function hashColor(str: string): string {
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash)
  }
  return CONTRIB_COLORS[Math.abs(hash) % CONTRIB_COLORS.length]
}

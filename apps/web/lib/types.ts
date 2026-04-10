/* ── Shared types from backend Pydantic schemas ─────────────────── */

export interface Workspace {
  id: string
  name: string
  slug: string
  visibility: 'private' | 'org' | 'public'
  created_at: string
}

export interface Memory {
  id: string
  workspace_id: string
  content: string
  tags: string[]
  memory_type?: string
  category?: string
  version: number
  current_version: boolean
  parent_memory_id?: string
  importance_score?: number
  confidence_score?: number
  created_at: string
  deleted_at?: string
  attribution?: Attribution
  relations?: MemoryRelation[]
  access_level?: string
  is_latest?: boolean
  source_type?: string
}

export interface MemoryVersion {
  id: string
  version: number
  is_current: boolean
  content: string
  created_at: string
  parent_id?: string
}

export interface Contributor {
  id: string
  name: string
  contribution_pct: number
  contribution_type: string
  created_at: string
}

export interface Attribution {
  memory_id: string
  contributors: Contributor[]
  creation_type: string
  edit_history: AttributionEdit[]
}

export interface AttributionEdit {
  editor_id: string
  editor_name: string
  action_type: string
  edit_position: number
  content_before?: string
  content_after?: string
  created_at: string
}

export interface MemoryRelation {
  source_memory_id: string
  target_memory_id: string
  relation_type: string
  confidence: number
}

/* ── Search ───────────────────────────────────────────────────────── */

export interface SearchResult {
  memory_id: string
  content: string
  relevance_score: number
  attribution?: Attribution
  relations?: MemoryRelation[]
  access_level?: string
  tags: string[]
  created_at: string
  is_latest: boolean
  workspace_id: string
}

export interface SearchResponse {
  results: SearchResult[]
  total_found: number
  search_latency_ms: number
  query_embedding_cached: boolean
  mode: string
}

export interface SearchFilters {
  workspace_ids?: string[]
  mode?: 'hybrid' | 'semantic' | 'keyword'
  limit?: number
  include_attribution?: boolean
  tags?: string[]
  memory_type?: string
}

/* ── Jobs ─────────────────────────────────────────────────────────── */

export type JobStatus =
  | 'queued'
  | 'extracting'
  | 'chunking'
  | 'extracting_facts'
  | 'embedding'
  | 'attributing'
  | 'indexing'
  | 'completed'
  | 'failed'

export interface Job {
  job_id: string
  status: JobStatus
  memories_created?: number
  processing_time_ms?: number
  error?: string
  created_at: string
  completed_at?: string
}

/* ── Conflicts ─────────────────────────────────────────────────────── */

export type ConflictStatus = 'open' | 'under_review' | 'resolved' | 'deferred'
export type ResolutionType = 'kept_a' | 'kept_b' | 'merged' | 'split' | 'deferred'

export interface ConflictMemory {
  id: string
  content: string
}

export interface Conflict {
  id: string
  memory_a: ConflictMemory
  memory_b: ConflictMemory
  memory_a_id: string
  memory_b_id: string
  memory_a_content?: string
  memory_b_content?: string
  conflict_type: string
  conflict_score?: number
  similarity_score?: number
  severity?: string
  explanation?: string
  status: ConflictStatus
  suggested_resolution?: {
    resolution_type: string
    reasoning: string
    confidence: number
  }
  reviewed_by?: string
  reviewed_at?: string
  revisit_at?: string
  created_at: string
}

/* ── Analytics ─────────────────────────────────────────────────────── */

export interface AnalyticsOverview {
  total_memories: number
  total_contributors: number
  memories_created_last_30_days: number
  open_conflicts: number
  knowledge_health_score: number
  health_breakdown?: {
    coverage: number
    freshness: number
    conflict: number
    attribution: number
  }
  top_contributors: TopContributor[]
  recent_activity: ActivityEvent[]
}

export interface TopContributor {
  user_id: string
  name: string
  count: number
  contribution_pct?: number
}

export interface ActivityEvent {
  event_type: string
  description: string
  user_name?: string
  user_id?: string
  created_at: string
}

export interface ContributorMapEntry {
  user_id: string
  name: string
  total_memories_created: number
  total_memories_influenced: number
  avg_contribution_pct: number
  knowledge_domains: string[]
  collaboration_rate: number
  last_contribution_at: string
}

export interface KnowledgeGap {
  gap_type: 'single_contributor' | 'no_recent_update' | 'high_conflict_area'
  description: string
  affected_memories: number
  risk_level: 'HIGH' | 'MEDIUM' | 'LOW'
  recommendation: string
}

/* ── Handoff ──────────────────────────────────────────────────────── */

export interface HandoffMemoryItem {
  memory_id: string
  content: string
  importance_score: number
  tier: 1 | 2 | 3
  suggested_successor_id?: string
  suggested_successor_name?: string
  successor_confidence?: number
}

export interface HandoffSummary {
  handoff_record_id: string
  departing_user_id: string
  departing_user_name: string
  total_memories: number
  tier_1_critical: HandoffMemoryItem[]
  tier_2_important: HandoffMemoryItem[]
  tier_3_standard_count: number
  handoff_expires_at?: string
}

/* ── User / Team ──────────────────────────────────────────────────── */

export interface TeamMember {
  id: string
  user_id: string
  name: string
  email: string
  role: 'owner' | 'admin' | 'member' | 'viewer'
  status: 'active' | 'departing' | 'departed'
  joined_at: string
  departed_at?: string
}

export interface UserProfile {
  id: string
  user_id: string
  display_name: string
  email: string
  role: string
}

/* ── API error ─────────────────────────────────────────────────────── */

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status?: number
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

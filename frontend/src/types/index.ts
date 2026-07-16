export interface JobInfo {
  id: string
  filename: string
  status: string
  progress: number
  total_shots: number | null
  duration_sec: number | null
  error_message: string | null
  category: string | null
  overview_text?: string | null
  created_at: string
  updated_at: string
  deleted_at: string | null
}

export interface DimensionInfo {
  dimension_name: string
  score: number | null
  label: string | null
  notes: string | null
}

export interface ShotInfo {
  id: number
  shot_number: number
  start_time_sec: number
  end_time_sec: number
  keyframe_paths: string
  status: string
  overall_notes: string | null
  analysis_text: string | null
  techniques_json: string | null
  dimensions: DimensionInfo[]
}

export interface ShotProgressInfo {
  id: number
  shot_number: number
  start_time_sec: number
  end_time_sec: number
  keyframe_paths: string
  status: string
  analysis_text: string | null
}

export interface JobDetail extends JobInfo {
  overview_text: string | null
  shots: ShotInfo[]
  transcript_segments: TranscriptSegment[]
}

export interface JobSummary extends JobInfo {
  overview_text: string | null
}

export interface JobShotsPage {
  shots_total: number
  shot_offset: number
  shot_limit: number
  shots_returned: number
  shots_truncated: boolean
  shots: ShotInfo[]
}

export interface JobProgress extends JobInfo {
  shots_total: number
  completed_shots: number
  failed_shots: number
  pending_shots: number
  shot_offset: number
  shot_limit: number
  shots_returned: number
  shots_truncated: boolean
  shots: ShotProgressInfo[]
}

export interface TokenUsageSource {
  source: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface TokenUsageSummary {
  path: string
  exists: boolean
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  by_source: TokenUsageSource[]
}

export interface TranscriptSegment {
  id: number
  start_sec: number
  end_sec: number
  text: string
}

export interface StoryboardShot {
  shot_number: number
  duration_sec: number
  description: string
  camera_movement: string
  bgm_note: string
  reference_from: string
  image_prompt: string
  image_url?: string | null
  image_path?: string | null
  image_status?: string | null
  image_error?: string | null
  image_updated_at?: string | null
}

export interface StoryboardResult {
  id?: string
  title: string
  total_duration_sec: number
  shots: StoryboardShot[]
  full_notes: string
  reference_job_ids?: string[]
  reference_shot_ids?: number[]
}

export interface StoryboardHistoryItem {
  id: string
  title: string
  brief: string
  total_duration_sec: number | null
  shot_count: number
  created_at: string
}

export interface StoryboardDetail extends StoryboardResult {
  id: string
  brief: string
  reference_job_ids: string[]
  reference_shot_ids: number[]
  created_at: string
}

export interface StoryboardGenerationTask {
  id: string
  brief: string
  reference_job_ids: string[]
  reference_shot_ids: number[]
  target_duration_sec: number | null
  status: "queued" | "collecting" | "generating" | "saving" | "completed" | "failed" | string
  progress: number
  message: string | null
  storyboard_id: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface ReferenceBoardItem {
  shot_id: number
  job_id: string
  job_filename: string
  job_category: string | null
  shot_number: number
  start_time_sec: number
  end_time_sec: number
  keyframe_paths: string
  status: string
  overall_notes: string | null
  analysis_text: string | null
  techniques_json: string | null
  dimensions: DimensionInfo[]
  created_at: string
}

export interface ReferenceBoardPage {
  total: number
  items: ReferenceBoardItem[]
}

export interface CategoryList {
  categories: string[]
}

export interface SystemSettingResponse {
  key: string
  value: string | null
  description: string | null
  is_secret: boolean
  updated_at: string
}

export interface ConnectivityResponse {
  status: string
  message: string
  engine: "analysis" | "storyboard" | "image" | string
  checked_at: string
}

export interface ConnectivityTestPayload {
  engine: "analysis" | "storyboard" | "image"
  api_key?: string
  model: string
  base_url: string
}

export interface OrphanJobDir {
  id: string
  path: string
  file_count: number
  size_bytes: number
  modified_at: string | null
  has_report: boolean
  has_original: boolean
  has_playback: boolean
}

export interface DuplicateFilename {
  filename: string
  count: number
  job_ids: string[]
}

export interface LegacyDataRoot {
  path: string
  db_exists: boolean
  jobs_dir_exists: boolean
}

export interface DataDiagnostics {
  data_root: string
  db_path: string
  jobs_dir: string
  logs_dir: string
  schema_version: number
  active_jobs: number
  deleted_jobs: number
  disk_job_dirs: number
  orphan_archive_dir: string
  orphan_job_dirs: OrphanJobDir[]
  duplicate_filenames: DuplicateFilename[]
  legacy_roots: LegacyDataRoot[]
}

export interface ArchiveOrphanResponse {
  archived: boolean
  id: string
  archived_path: string
}

export interface JobInfo {
  id: string
  filename: string
  status: string
  progress: number
  total_shots: number | null
  duration_sec: number | null
  error_message: string | null
  category: string | null
  overview_text: string | null
  created_at: string
  updated_at: string
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

export interface JobDetail extends JobInfo {
  shots: ShotInfo[]
  transcript_segments: TranscriptSegment[]
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
}

export interface StoryboardResult {
  title: string
  total_duration_sec: number
  shots: StoryboardShot[]
  full_notes: string
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
  created_at: string
}

export interface StoryboardGenerationTask {
  id: string
  brief: string
  reference_job_ids: string[]
  target_duration_sec: number | null
  status: "queued" | "collecting" | "generating" | "saving" | "completed" | "failed" | string
  progress: number
  message: string | null
  storyboard_id: string | null
  error_message: string | null
  created_at: string
  updated_at: string
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

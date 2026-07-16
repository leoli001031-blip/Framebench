from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional
from datetime import datetime


class JobListResponse(BaseModel):
    id: str
    filename: str
    status: str
    progress: float
    total_shots: Optional[int] = None
    duration_sec: Optional[float] = None
    error_message: Optional[str] = None
    category: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobResponse(JobListResponse):
    overview_text: Optional[str] = None


class JobDetailResponse(JobResponse):
    pass


class UpdateJobRequest(BaseModel):
    category: Optional[str] = None
    filename: Optional[str] = None


class RenameCategoryRequest(BaseModel):
    old_name: str = Field(..., min_length=1, max_length=80)
    new_name: str = Field(..., min_length=1, max_length=80)


class DimensionResponse(BaseModel):
    dimension_name: str
    score: Optional[int] = None
    label: Optional[str] = None
    notes: Optional[str] = None

    model_config = {"from_attributes": True}


class ShotResponse(BaseModel):
    id: int
    shot_number: int
    start_time_sec: float
    end_time_sec: float
    keyframe_paths: str
    status: str
    overall_notes: Optional[str] = None
    analysis_text: Optional[str] = None
    techniques_json: Optional[str] = None
    dimensions: list[DimensionResponse] = []

    model_config = {"from_attributes": True}


class ShotProgressResponse(BaseModel):
    id: int
    shot_number: int
    start_time_sec: float
    end_time_sec: float
    keyframe_paths: str
    status: str
    analysis_text: Optional[str] = None

    model_config = {"from_attributes": True}


class TranscriptSegmentResponse(BaseModel):
    id: int
    start_sec: float
    end_sec: float
    text: str

    model_config = {"from_attributes": True}


class JobWithShotsResponse(JobResponse):
    shots: list[ShotResponse] = []
    transcript_segments: list[TranscriptSegmentResponse] = []

    model_config = {"from_attributes": True}


class JobShotsPageResponse(BaseModel):
    shots_total: int = 0
    shot_offset: int = 0
    shot_limit: int = 0
    shots_returned: int = 0
    shots_truncated: bool = False
    shots: list[ShotResponse] = []

    model_config = {"from_attributes": True}


class JobProgressResponse(JobListResponse):
    shots_total: int = 0
    completed_shots: int = 0
    failed_shots: int = 0
    pending_shots: int = 0
    shot_offset: int = 0
    shot_limit: int = 0
    shots_returned: int = 0
    shots_truncated: bool = False
    shots: list[ShotProgressResponse] = []

    model_config = {"from_attributes": True}


class GenerateStoryboardRequest(BaseModel):
    brief: str = Field(..., min_length=1, max_length=5000)
    reference_job_ids: list[str] = Field(default_factory=list, max_length=20)
    reference_shot_ids: list[int] = Field(default_factory=list, max_length=20)
    target_duration_sec: Optional[int] = None
    generate_images: bool = True
    client_task_id: Optional[str] = Field(default=None, min_length=1, max_length=80)


class ReferenceBoardItemResponse(BaseModel):
    shot_id: int
    job_id: str
    job_filename: str
    job_category: Optional[str] = None
    shot_number: int
    start_time_sec: float
    end_time_sec: float
    keyframe_paths: str
    status: str
    overall_notes: Optional[str] = None
    analysis_text: Optional[str] = None
    techniques_json: Optional[str] = None
    dimensions: list[DimensionResponse] = []
    created_at: datetime


class ReferenceBoardListResponse(BaseModel):
    total: int = 0
    items: list[ReferenceBoardItemResponse] = []


class StoryboardShot(BaseModel):
    shot_number: int = Field(..., ge=1)
    duration_sec: float = Field(..., gt=0)
    description: str = Field(..., min_length=1)
    camera_movement: str
    bgm_note: str
    reference_from: str
    image_prompt: str = ""
    image_url: Optional[str] = None
    image_status: Optional[str] = None
    image_error: Optional[str] = None


class StoryboardResponse(BaseModel):
    title: str = Field(..., min_length=1)
    total_duration_sec: float = Field(..., gt=0)
    shots: list[StoryboardShot] = Field(..., min_length=1)
    full_notes: str


class UploadResponse(BaseModel):
    job_id: str
    filename: str
    status: str


class StartResponse(BaseModel):
    job_id: str
    status: str


class DeleteResponse(BaseModel):
    deleted: bool


class OrphanJobDirResponse(BaseModel):
    id: str
    path: str
    file_count: int
    size_bytes: int
    modified_at: Optional[datetime] = None
    has_report: bool = False
    has_original: bool = False
    has_playback: bool = False


class DuplicateFilenameResponse(BaseModel):
    filename: str
    count: int
    job_ids: list[str]


class LegacyDataRootResponse(BaseModel):
    path: str
    db_exists: bool
    jobs_dir_exists: bool


class DataDiagnosticsResponse(BaseModel):
    data_root: str
    db_path: str
    jobs_dir: str
    logs_dir: str
    schema_version: int = 0
    active_jobs: int
    deleted_jobs: int
    disk_job_dirs: int
    orphan_archive_dir: str
    orphan_job_dirs: list[OrphanJobDirResponse] = []
    duplicate_filenames: list[DuplicateFilenameResponse] = []
    legacy_roots: list[LegacyDataRootResponse] = []


class ArchiveOrphanJobDirResponse(BaseModel):
    archived: bool
    id: str
    archived_path: str


class TokenUsageSourceResponse(BaseModel):
    source: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class TokenUsageSummaryResponse(BaseModel):
    path: str
    exists: bool = False
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    by_source: list[TokenUsageSourceResponse] = []


class CategoryListResponse(BaseModel):
    categories: list[str]


# Storyboard history
class StoryboardHistoryItem(BaseModel):
    id: str
    title: str
    brief: str
    total_duration_sec: Optional[float] = None
    shot_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class StoryboardShotDetail(BaseModel):
    shot_number: int
    duration_sec: float
    description: str
    camera_movement: str
    bgm_note: str
    reference_from: str
    image_prompt: Optional[str] = None
    image_url: Optional[str] = None
    image_status: Optional[str] = None
    image_error: Optional[str] = None
    image_updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class StoryboardDetailResponse(BaseModel):
    id: str
    title: str
    brief: str
    total_duration_sec: Optional[float] = None
    full_notes: Optional[str] = None
    reference_job_ids: list[str] = []
    reference_shot_ids: list[int] = []
    shots: list[StoryboardShotDetail] = []
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("reference_job_ids", mode="before")
    @classmethod
    def parse_reference_ids(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v

    @field_validator("reference_shot_ids", mode="before")
    @classmethod
    def parse_reference_shot_ids(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v


class StoryboardGenerationTaskResponse(BaseModel):
    id: str
    brief: str
    reference_job_ids: list[str] = []
    reference_shot_ids: list[int] = []
    target_duration_sec: Optional[int] = None
    status: str
    progress: float = 0.0
    message: Optional[str] = None
    storyboard_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("reference_job_ids", mode="before")
    @classmethod
    def parse_reference_ids(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v

    @field_validator("reference_shot_ids", mode="before")
    @classmethod
    def parse_reference_shot_ids(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v


class SystemSettingResponse(BaseModel):
    key: str
    value: Optional[str] = None
    description: Optional[str] = None
    is_secret: bool = False
    updated_at: datetime

    model_config = {"from_attributes": True}


class UpdateSettingRequest(BaseModel):
    value: str = Field(..., max_length=8192)


class ConnectivityTestRequest(BaseModel):
    engine: Literal["analysis", "storyboard", "image"] = "analysis"
    api_key: Optional[str] = Field(default=None, max_length=8192)
    model: Optional[str] = Field(default=None, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=2048)


class ImageConnectivityResponse(BaseModel):
    status: str
    message: str
    engine: str
    checked_at: datetime

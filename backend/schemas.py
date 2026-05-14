from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


class JobResponse(BaseModel):
    id: str
    filename: str
    status: str
    progress: float
    total_shots: Optional[int] = None
    duration_sec: Optional[float] = None
    error_message: Optional[str] = None
    category: Optional[str] = None
    overview_text: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobDetailResponse(JobResponse):
    pass


class UpdateJobRequest(BaseModel):
    category: Optional[str] = None
    filename: Optional[str] = None


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


class GenerateStoryboardRequest(BaseModel):
    brief: str = Field(..., min_length=1, max_length=5000)
    reference_job_ids: list[str] = Field(..., min_length=1, max_length=20)
    target_duration_sec: Optional[int] = None
    client_task_id: Optional[str] = Field(default=None, min_length=1, max_length=80)


class StoryboardShot(BaseModel):
    shot_number: int
    duration_sec: float
    description: str
    camera_movement: str
    bgm_note: str
    reference_from: str
    image_prompt: str = ""


class StoryboardResponse(BaseModel):
    title: str
    total_duration_sec: float
    shots: list[StoryboardShot]
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

    model_config = {"from_attributes": True}


class StoryboardDetailResponse(BaseModel):
    id: str
    title: str
    brief: str
    total_duration_sec: Optional[float] = None
    full_notes: Optional[str] = None
    reference_job_ids: list[str] = []
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


class StoryboardGenerationTaskResponse(BaseModel):
    id: str
    brief: str
    reference_job_ids: list[str] = []
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


class SystemSettingResponse(BaseModel):
    key: str
    value: Optional[str] = None
    description: Optional[str] = None
    is_secret: bool = False
    updated_at: datetime

    model_config = {"from_attributes": True}


class UpdateSettingRequest(BaseModel):
    value: str

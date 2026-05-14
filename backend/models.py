import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from backend.database import Base


def _now():
    return datetime.now(timezone.utc)


def gen_uuid():
    return str(uuid.uuid4())


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=gen_uuid)
    filename = Column(String, nullable=False)
    video_path = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    # pending -> preprocessing -> preprocessing_done -> analyzing -> completed / failed
    progress = Column(Float, default=0.0)
    total_shots = Column(Integer, nullable=True)
    duration_sec = Column(Float, nullable=True)
    error_message = Column(String, nullable=True)
    category = Column(String, nullable=True)
    overview_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    shots = relationship("Shot", back_populates="job", cascade="all, delete-orphan")
    transcript_segments = relationship("TranscriptSegment", back_populates="job", cascade="all, delete-orphan")


class Shot(Base):
    __tablename__ = "shots"
    __table_args__ = (UniqueConstraint("job_id", "shot_number"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    shot_number = Column(Integer, nullable=False)
    start_time_sec = Column(Float, nullable=False)
    end_time_sec = Column(Float, nullable=False)
    keyframe_paths = Column(Text, nullable=False)  # JSON array
    status = Column(String, default="pending")  # pending | analyzing | completed | failed
    overall_notes = Column(Text, nullable=True)
    analysis_text = Column(Text, nullable=True)  # Free-form analysis for reference shots
    techniques_json = Column(Text, nullable=True)  # JSON array of techniques to reference
    created_at = Column(DateTime, default=_now)

    job = relationship("Job", back_populates="shots")
    dimensions = relationship("Dimension", back_populates="shot", cascade="all, delete-orphan")


class Dimension(Base):
    __tablename__ = "dimensions"
    __table_args__ = (UniqueConstraint("shot_id", "dimension_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    shot_id = Column(Integer, ForeignKey("shots.id", ondelete="CASCADE"), nullable=False)
    dimension_name = Column(String, nullable=False)
    score = Column(Integer, nullable=True)
    label = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)

    shot = relationship("Shot", back_populates="dimensions")


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    start_sec = Column(Float, nullable=False)
    end_sec = Column(Float, nullable=False)
    text = Column(Text, nullable=False)

    job = relationship("Job", back_populates="transcript_segments")


class Storyboard(Base):
    __tablename__ = "storyboards"

    id = Column(String, primary_key=True, default=gen_uuid)
    title = Column(String, nullable=False)
    brief = Column(Text, nullable=False)
    full_notes = Column(Text, nullable=True)
    total_duration_sec = Column(Float, nullable=True)
    reference_job_ids = Column(Text, nullable=False)  # JSON array
    created_at = Column(DateTime, default=_now)

    shots = relationship("StoryboardShot", back_populates="storyboard", cascade="all, delete-orphan")


class StoryboardShot(Base):
    __tablename__ = "storyboard_shots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_id = Column(String, ForeignKey("storyboards.id", ondelete="CASCADE"), nullable=False)
    shot_number = Column(Integer, nullable=False)
    duration_sec = Column(Float, nullable=False)
    description = Column(Text, nullable=False)
    camera_movement = Column(String, nullable=True)
    bgm_note = Column(Text, nullable=True)
    reference_from = Column(String, nullable=True)
    image_prompt = Column(Text, nullable=True)

    storyboard = relationship("Storyboard", back_populates="shots")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    description = Column(String, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

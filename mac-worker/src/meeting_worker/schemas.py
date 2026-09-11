from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class JobStage(StrEnum):
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    TRANSCRIBING = "transcribing"
    DIARIZING = "diarizing"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Evidence(BaseModel):
    segment_ids: list[str] = Field(default_factory=list)
    quote: str | None = None
    speaker: str | None = None
    start: float | None = None
    end: float | None = None


class ProtocolItem(BaseModel):
    id: str
    text: str
    evidence: Evidence = Field(default_factory=Evidence)
    source_check: Literal["passed", "failed", "unavailable"] = "unavailable"
    review_status: Literal[
        "unreviewed", "needs_review", "human_confirmed", "rejected"
    ] = "unreviewed"


class Topic(ProtocolItem):
    title: str


class ActionItem(BaseModel):
    id: str
    task: str
    assignee: str | None = None
    deadline_text: str | None = None
    deadline_date: date | None = None
    priority: Literal["low", "medium", "high", "urgent", "not_specified"] = (
        "not_specified"
    )
    evidence: Evidence = Field(default_factory=Evidence)
    source_check: Literal["passed", "failed", "unavailable"] = "unavailable"
    review_status: Literal[
        "unreviewed", "needs_review", "human_confirmed", "rejected"
    ] = "unreviewed"

    @field_validator("assignee", "deadline_text", mode="before")
    @classmethod
    def normalize_unknown(cls, value):
        if isinstance(value, str) and value.strip().lower() in {"null", "none", "unknown", "not specified", "not_specified", "n/a"}:
            return None
        return value


class MeetingMetadata(BaseModel):
    title: str = "Meeting"
    meeting_date: date | None = None
    timezone: str | None = None
    language: str = "auto"
    duration_seconds: float | None = None
    participants: list[str] = Field(default_factory=list)


class SummarySource(BaseModel):
    """Provenance for the string at the same index in executive_summary."""
    item_id: str
    evidence: Evidence


class MeetingProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    metadata: MeetingMetadata
    executive_summary: list[str] = Field(default_factory=list, max_length=5)
    executive_summary_sources: list[SummarySource] = Field(default_factory=list, max_length=5)
    topics: list[Topic] = Field(default_factory=list)
    decisions: list[ProtocolItem] = Field(default_factory=list)
    open_questions: list[ProtocolItem] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    risks: list[ProtocolItem] = Field(default_factory=list)


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: str
    start: float | None = None
    end: float | None = None
    text: str = Field(max_length=1000000)
    speaker: str | None = None
    language: str | None = None

    @model_validator(mode="after")
    def valid_interval(self):
        if self.start is not None and self.start < 0:
            raise ValueError("Negative transcript start")
        if self.end is not None and (self.end < 0 or (self.start is not None and self.end < self.start)):
            raise ValueError("Invalid transcript interval")
        return self


class Transcript(BaseModel):
    schema_version: str = "1.0"
    language: str = "auto"
    model: str
    raw_text: str
    segments: list[TranscriptSegment]


class JobManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    meeting_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="Meeting", min_length=1, max_length=200)
    language_mode: Literal["kk_ru", "en", "auto"] = "auto"
    output_language: Literal["same", "kk", "ru", "en"] = "same"
    meeting_date: date | None = None
    timezone: str | None = None
    diarization: bool = False
    min_speakers: int | None = Field(default=None, ge=1, le=20)
    max_speakers: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def speaker_bounds(self):
        if self.min_speakers is not None and self.max_speakers is not None and self.min_speakers > self.max_speakers:
            raise ValueError("Minimum speakers cannot exceed maximum")
        return self


class JobRecord(BaseModel):
    id: str
    meeting_id: str
    stage: JobStage
    progress_current: int = 0
    progress_total: int = 0
    source_kind: Literal["audio", "text"]
    source_path: str
    source_sha256: str
    manifest: JobManifest
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    error_message: str | None = None
    result_path: str | None = None


class JobResult(BaseModel):
    job: JobRecord
    transcript: Transcript
    protocol: MeetingProtocol
    exports: dict[str, str]

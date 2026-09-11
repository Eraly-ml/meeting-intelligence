from typing import Annotated, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MeetingStart(StrictModel):
    meeting_id: UUID
    title: str = Field(min_length=1, max_length=200)


class Segment(StrictModel):
    sequence: int = Field(ge=1, strict=True)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker: str = Field(pattern=r"^(local|remote|unknown|speaker_[0-9]{2,4})$")
    text: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end < self.start:
            raise ValueError("end must be at or after start")
        if not self.text.strip():
            raise ValueError("text must not be blank")
        return self


class SegmentBatch(StrictModel):
    segments: List[Segment] = Field(min_length=1, max_length=128)


class MeetingEnd(StrictModel):
    last_sequence: int = Field(ge=0, strict=True)


class RecordingStart(StrictModel):
    title: str = Field(default="New recording", min_length=1, max_length=200)


EvidenceSequence = Annotated[int, Field(strict=True, ge=1)]


class Decision(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    evidence: List[EvidenceSequence] = Field(min_length=1, max_length=16)


class ActionItem(StrictModel):
    task: str = Field(min_length=1, max_length=500)
    owner: Optional[str] = Field(max_length=120)
    due: Optional[str] = Field(max_length=120)
    evidence: List[EvidenceSequence] = Field(min_length=1, max_length=16)


class Report(StrictModel):
    summary: str = Field(max_length=2400)
    decisions: List[Decision] = Field(max_length=80)
    action_items: List[ActionItem] = Field(max_length=80)
    open_questions: List[str] = Field(max_length=80)
    topics: List[str] = Field(max_length=80)
    risks: List[str] = Field(max_length=80)

    def check_evidence(self, allowed):
        for item in self.decisions + self.action_items:
            if any(type(ref) is not int or ref not in allowed for ref in item.evidence):
                raise ValueError("Report references transcript sequences that were not provided")
        return self

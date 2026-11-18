"""请求/响应模型。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class ScenarioCreate(BaseModel):
    name: str = "临时情景"
    population_as_of: date
    departure_time: datetime
    max_trip_minutes: int = Field(gt=0, le=600)
    road_cutoff: datetime
    boundary_id: Optional[str] = None
    viewer_id: str = "analyst-ui"
    note: Optional[str] = None


class ViewerRelease(BaseModel):
    viewer_id: str


class CompareRequest(BaseModel):
    task_a_id: str
    task_b_id: str


class PublishRequest(BaseModel):
    task_a_id: str
    task_b_id: str
    title: str
    created_by: str = "analyst"


class PrecomputeRequest(BaseModel):
    population_as_of: date
    base_day: datetime
    cutoff: datetime
    deadline_minutes: int = 60
    offsets_minutes: list[int] = [0, 30, 60, 90, 120]
    boundary_id: Optional[str] = None


class InvalidateRequest(BaseModel):
    edge_id: str
    reason: str = "道路事件修订"


class RebuildRequest(BaseModel):
    new_cutoff: datetime


class EventCreate(BaseModel):
    """临时封路/限速：立即参与后续即时计算。"""

    event_id: str
    event_type: str  # closure | speed_limit
    edge_id: str
    effective_from: datetime
    effective_to: Optional[datetime] = None
    speed_limit_kmh: Optional[float] = None
    source_title: str = "临时上报事件"
    raw_text: str = ""


# ------------------------------------------------------ 安置点情报接口 --
class ShelterReportRequest(BaseModel):
    task_id: str
    policy: str = Field(default="auto", pattern="^(auto|manual)$")
    evidence_cutoff: Optional[datetime] = None
    evaluation_time: Optional[datetime] = None
    created_by: str = "analyst"


class AnnouncementCreate(BaseModel):
    """公告接入：允许显式指定 received_at 以模拟迟到补传。"""

    announcement_id: str
    kind: str = Field(pattern="^(open|capacity|revoke|coordinate)$")
    site_name: str
    address: Optional[str] = None
    geom_x: Optional[float] = None
    geom_y: Optional[float] = None
    unit_raw: Optional[str] = None
    capacity_value: Optional[float] = None
    occupancy_value: Optional[float] = None
    occupancy_known: bool = True
    verified_at: Optional[datetime] = None
    proof_expires_at: Optional[datetime] = None
    open_from: Optional[datetime] = None
    open_to: Optional[datetime] = None
    effective_at: Optional[datetime] = None
    note: Optional[str] = None
    title: Optional[str] = None
    raw_text: str = ""
    received_at: Optional[datetime] = None
    source_occurred_at: Optional[datetime] = None


class EntityOverrideCreate(BaseModel):
    announcement_id: str
    action: str = Field(pattern="^(confirm|reject)$")
    master_id: Optional[str] = None
    note: Optional[str] = None
    created_by: str = "analyst"

from pydantic import BaseModel
from typing import List, Optional


class Box(BaseModel):
    id: str
    x: float
    y: float
    w: float
    h: float
    confidence: Optional[float] = None
    source: str = "detector"


class ApproveSheetRequest(BaseModel):
    project_id: str
    page_id: int
    boxes: List[Box]
    sheet_box: Optional[dict] = None


class Designer(BaseModel):
    discipline: str
    firm_name: str


class SkipSheetRequest(BaseModel):
    project_id: str
    page_id: int


class RedetectSheetRequest(BaseModel):
    project_id: str
    page_id: int


class SheetNumberPreviewRequest(BaseModel):
    project_id: str
    page_id: int
    sheet_box: dict


class DetailUpdateRequest(BaseModel):
    project_name: Optional[str] = None
    design_team: Optional[str] = None
    detail_title: Optional[str] = None
    detail_number: Optional[str] = None
    sheet_number: Optional[str] = None
    discipline: Optional[str] = None
    tags: Optional[List[str]] = None
    csi_divisions: Optional[List[str]] = None
    warnings: Optional[List[str]] = None
    summary: Optional[str] = None
    searchable_description: Optional[str] = None
    assembly_system_type: Optional[str] = None
    confidence_score: Optional[float] = None
    bookmarked: Optional[bool] = None
    notes: Optional[str] = None
    designers: Optional[List[Designer]] = None

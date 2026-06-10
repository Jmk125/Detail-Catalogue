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


class SkipSheetRequest(BaseModel):
    project_id: str
    page_id: int

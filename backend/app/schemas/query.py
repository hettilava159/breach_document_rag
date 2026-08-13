from typing import List, Optional, Dict
from pydantic import BaseModel, Field

class QueryRequest(BaseModel):
    question: str = Field(..., max_length=4000)
    document_id: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = Field(default=None, max_length=50)

class SourceChunk(BaseModel):
    text: str
    page_number: int
    score: float

class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceChunk]

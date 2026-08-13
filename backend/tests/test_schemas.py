import pytest
from pydantic import ValidationError
from app.schemas.query import QueryRequest, QueryResponse, SourceChunk


def test_query_request_accepts_minimal_payload():
    req = QueryRequest(question="What is the termination clause?")
    assert req.document_id is None
    assert req.history is None


def test_query_request_rejects_overlong_question():
    with pytest.raises(ValidationError):
        QueryRequest(question="x" * 4001)


def test_query_response_shape():
    resp = QueryResponse(
        answer="The contract terminates after 30 days.",
        sources=[SourceChunk(text="...", page_number=1, score=0.87)],
    )
    assert resp.sources[0].page_number == 1

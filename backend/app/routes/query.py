import json
import logging
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from app.database import Database
from app.dependencies import get_session_id
from app.rate_limiter import limiter
from app.schemas.query import QueryRequest
from app.services.vector_service import search_similar_chunks
from app.services.llm_service import generate_response_stream

logger = logging.getLogger("app.routes.query")
router = APIRouter(prefix="/query", tags=["query"])

@router.post("/")
@limiter.limit("30/minute")
async def query_document(request: Request, body: QueryRequest, session_id: str = Depends(get_session_id)):
    """
    Retrieves matching text chunks from MongoDB and streams the LLM response.
    Returns source citations in the 'X-Sources' header. Scoped to the caller's session.
    """
    if not body.question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question cannot be empty."
        )

    if body.document_id:
        try:
            doc_obj_id = ObjectId(body.document_id)
        except InvalidId:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid document_id format."
            )

        # Ownership check: a session can only query documents it owns.
        documents_collection = Database.get_documents_collection()
        owned_doc = await documents_collection.find_one({"_id": doc_obj_id, "session_id": session_id})
        if not owned_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found."
            )

    try:
        # 1. Fetch relevant chunks using MongoDB Atlas Vector Search (or local similarity fallback)
        # We retrieve the top 20 matching chunks for better Contextual Retrieval performance
        chunks = await search_similar_chunks(
            query_text=body.question,
            limit=20,
            document_id=body.document_id,
            session_id=session_id
        )

        if not chunks:
            # If no text chunks are stored in the DB yet
            async def empty_generator():
                yield "No documents have been processed yet or no relevant information was found."
            return StreamingResponse(empty_generator(), media_type="text/plain")

        # 2. Serialize citations to pass to the client via custom HTTP headers
        sources = [
            {
                "document_id": chunk["document_id"],
                "text": chunk["text"],
                "context": chunk.get("context", ""),
                "page_number": chunk["page_number"],
                "score": round(chunk["score"], 4)
            }
            for chunk in chunks
        ]
        
        # 3. Create response stream generator
        response_generator = generate_response_stream(body.question, chunks, body.history)
        
        # We must expose 'X-Sources' header so the React frontend can read it via CORS
        headers = {
            "Access-Control-Expose-Headers": "X-Sources",
            "X-Sources": json.dumps(sources)
        }

        # 4. Stream the generated text back in real-time
        return StreamingResponse(
            response_generator,
            media_type="text/event-stream",
            headers=headers
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling query: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Query request failed due to an internal error."
        )

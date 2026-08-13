import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from typing import List
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, UploadFile, File, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from app.database import Database
from app.config import settings
from app.dependencies import get_session_id
from app.rate_limiter import limiter
from app.schemas.document import DocumentOut
from app.services.pdf_service import extract_pages_from_pdf, extract_metadata_from_pdf
from app.services.llm_service import extract_metadata_from_text, build_chunk_context, generate_document_summary
from app.services.vector_service import store_document_chunks
from app.services.agent_service import run_contract_audit
from app.services.report_service import generate_audit_pdf
from app.services.session_service import sweep_orphaned_uploads


UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_UPLOAD_SIZE_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
PDF_MAGIC_BYTES = b"%PDF-"

logger = logging.getLogger("app.routes.document")
router = APIRouter(prefix="/documents", tags=["documents"])


def _validate_object_id(document_id: str) -> ObjectId:
    """Validates a document_id is a well-formed ObjectId, raising a clean 400 otherwise."""
    try:
        return ObjectId(document_id)
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document ID format."
        )

async def process_pdf_background(
    document_id: str,
    file_bytes: bytes,
    session_id: str,
    expires_at: datetime,
    current_title: str = None,
    current_author: str = None,
    generate_context: bool = False
):
    """
    Background worker that extracts text, splits it into chunks, 
    generates embeddings, and stores them in MongoDB.
    Refines title/author metadata via LLM if missing.
    """
    try:
        documents_collection = Database.get_documents_collection()
        
        # 1. Parse PDF and extract page-by-page text (off the event loop - can be slow for large/degenerate PDFs)
        pages_data = await asyncio.to_thread(extract_pages_from_pdf, file_bytes)
        
        title = current_title
        author = current_author
        
        # Fallback to LLM to extract title/author from first page text if missing
        if pages_data and (not title or not author):
            first_page_text = pages_data[0]["text"]
            llm_meta = await extract_metadata_from_text(first_page_text)
            if llm_meta:
                if not title and llm_meta.get("title"):
                    title = llm_meta["title"]
                if not author and llm_meta.get("author"):
                    author = llm_meta["author"]
        
        # 2. Extract structured clauses from pages (with fallback to text splitting)
        from app.services.llm_service import extract_and_chunk_pdf_document
        chunks = await extract_and_chunk_pdf_document(pages_data)
        
        # 2b. Generate situational contexts for chunks if enabled.
        # Only one LLM call is made here (the overall document summary) — per-chunk
        # context is then built deterministically from that summary plus the
        # category/clause_reference metadata each chunk already has from structured
        # extraction, instead of spending one LLM call per chunk.
        if chunks and generate_context:
            logger.info(f"Generating situational context for {len(chunks)} chunks using {settings.LLM_PROVIDER}...")
            whole_document = "\n".join([page["text"] for page in pages_data])

            logger.info("Generating overall document summary...")
            doc_summary = await generate_document_summary(whole_document)
            logger.info(f"Summary generated: '{doc_summary}'")

            for chunk in chunks:
                chunk["context"] = build_chunk_context(
                    doc_summary, chunk.get("category", "General"), chunk.get("clause_reference", "N/A")
                )
            await documents_collection.update_one(
                {"_id": ObjectId(document_id)},
                {"$set": {"processing_progress": 80}}
            )
            logger.info("Context generation completed.")
        else:
            logger.info("Situational chunk context generation is disabled.")
        
        # 3. Generate embeddings and save chunks in MongoDB
        await store_document_chunks(
            document_id, chunks, session_id, expires_at, start_progress=80 if generate_context else 0
        )
        
        # 3b. Run Automated Legal Compliance Audit
        try:
            # Wait dynamically for MongoDB Atlas asynchronous search indexes to synchronize
            from app.services.vector_service import wait_for_atlas_indexing
            await wait_for_atlas_indexing(document_id, timeout_seconds=6)
                
            logger.info(f"Running automated compliance risk audit for document {document_id}...")
            audit_report = await run_contract_audit(document_id)
            audit_score = audit_report.get("overall_score", 100)
            has_audit = True
        except Exception as audit_err:
            logger.error(f"Failed to generate compliance audit: {audit_err}")
            audit_report = None
            audit_score = None
            has_audit = False

        # 4. Mark document status as processed and update metadata
        update_data = {
            "status": "processed",
            "processing_progress": 100,
            "title": title,
            "author": author,
            "has_audit": has_audit
        }
        if has_audit:
            update_data["audit_score"] = audit_score
            update_data["audit_report"] = audit_report

        await documents_collection.update_one(
            {"_id": ObjectId(document_id)},
            {"$set": update_data}
        )
        logger.info(f"Background processing and legal audit completed successfully for document {document_id}")
    except Exception as e:
        logger.error(f"Failed to process document {document_id} in background: {e}")
        try:
            documents_collection = Database.get_documents_collection()
            await documents_collection.update_one(
                {"_id": ObjectId(document_id)},
                {"$set": {
                    "status": "error",
                    "processing_progress": 0,
                    "error_message": str(e)
                }}
            )
        except Exception as update_err:
            # BackgroundTasks silently drops exceptions - if this update itself fails,
            # the document would otherwise be stuck in "processing" forever with no trace.
            logger.critical(
                f"Failed to mark document {document_id} as errored after a processing failure: {update_err}"
            )


@router.post("/", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("10/minute")
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    generate_context: bool = settings.GENERATE_SITUATIONAL_CONTEXT,
    session_id: str = Depends(get_session_id)
):
    """
    Uploads a PDF document and starts an asynchronous background processing task.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only PDF files are supported."
        )

    # Best-effort: remove any local PDF files left behind by documents that
    # MongoDB's TTL index has already expired (TTL can't reach local disk).
    try:
        await sweep_orphaned_uploads()
    except Exception as sweep_err:
        logger.warning(f"Orphaned upload sweep failed: {sweep_err}")

    try:
        # Read file bytes in memory
        file_bytes = await file.read()
        file_size = len(file_bytes)

        if file_size > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum allowed size is {settings.MAX_UPLOAD_SIZE_MB}MB."
            )

        if not file_bytes.startswith(PDF_MAGIC_BYTES):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File does not appear to be a valid PDF."
            )

        # Extract initial PDF metadata off the event loop (parsing can be slow for large/malformed files)
        pdf_meta = await asyncio.to_thread(extract_metadata_from_pdf, file_bytes)
        title = pdf_meta.get("title")
        author = pdf_meta.get("author")
        
        # Insert initial metadata in MongoDB with 'processing' status
        uploaded_at = datetime.utcnow()
        expires_at = uploaded_at + timedelta(hours=settings.SESSION_MAX_AGE_HOURS)
        doc_data = {
            "filename": file.filename,
            "file_size": file_size,
            "status": "processing",
            "uploaded_at": uploaded_at,
            "expires_at": expires_at,
            "session_id": session_id,
            "title": title,
            "author": author,
            "processing_progress": 0,
            "has_context": generate_context
        }

        documents_collection = Database.get_documents_collection()
        insert_result = await documents_collection.insert_one(doc_data)
        doc_id = str(insert_result.inserted_id)

        # Save raw PDF file to local uploads directory
        file_path = os.path.join(UPLOAD_DIR, f"{doc_id}.pdf")
        with open(file_path, "wb") as f:
            f.write(file_bytes)

        # Delegate chunking, embedding generation, and metadata refinement to background task
        background_tasks.add_task(
            process_pdf_background, doc_id, file_bytes, session_id, expires_at, title, author, generate_context
        )

        return DocumentOut(
            id=doc_id,
            filename=file.filename,
            file_size=file_size,
            uploaded_at=doc_data["uploaded_at"],
            status="processing",
            processing_progress=0,
            title=title,
            author=author,
            has_context=generate_context
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initiating upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upload failed due to an internal error. Please try again."
        )


@router.get("/", response_model=List[DocumentOut])
async def list_documents(session_id: str = Depends(get_session_id)):
    """
    Retrieves all documents uploaded by the caller's session, sorted by upload date descending.
    """
    try:
        documents_collection = Database.get_documents_collection()
        cursor = documents_collection.find({"session_id": session_id}).sort("uploaded_at", -1)

        docs = []
        async for doc in cursor:
            docs.append(DocumentOut(
                id=str(doc["_id"]),
                filename=doc["filename"],
                file_size=doc["file_size"],
                uploaded_at=doc["uploaded_at"],
                status=doc["status"],
                processing_progress=doc.get("processing_progress", 0 if doc["status"] == "processing" else 100),
                title=doc.get("title"),
                author=doc.get("author"),
                has_context=doc.get("has_context", False),
                audit_score=doc.get("audit_score"),
                has_audit=doc.get("has_audit", False),
                audit_report=doc.get("audit_report")
            ))
        return docs
    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch documents due to an internal error."
        )


@router.delete("/{document_id}", status_code=status.HTTP_200_OK)
async def delete_document(document_id: str, session_id: str = Depends(get_session_id)):
    """
    Deletes a document metadata and all its associated text chunk embeddings from MongoDB.
    Scoped to the caller's session - a session can only delete its own documents.
    """
    doc_obj_id = _validate_object_id(document_id)
    try:
        # 1. Delete document metadata (only if owned by this session)
        documents_collection = Database.get_documents_collection()
        delete_doc_result = await documents_collection.delete_one({"_id": doc_obj_id, "session_id": session_id})

        if delete_doc_result.deleted_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found."
            )
            
        # 1b. Delete local PDF file if it exists
        file_path = os.path.join(UPLOAD_DIR, f"{document_id}.pdf")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Deleted local file: {file_path}")
            except Exception as file_err:
                logger.warning(f"Failed to delete local file {file_path}: {file_err}")
            
        # 2. Delete all associated vector chunks
        chunks_collection = Database.get_chunks_collection()
        delete_chunks_result = await chunks_collection.delete_many({"document_id": doc_obj_id})
        
        logger.info(f"Deleted document {document_id} and {delete_chunks_result.deleted_count} associated chunks.")
        return {"status": "success", "message": f"Successfully deleted document and its {delete_chunks_result.deleted_count} chunks."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document {document_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete document due to an internal error."
        )


@router.get("/{document_id}/pdf")
async def get_document_pdf(document_id: str, session_id: str = Depends(get_session_id)):
    """
    Serves the raw PDF file from the local uploads directory.
    Scoped to the caller's session via sid query param or X-Session-Id header.
    """
    # Validating document_id as an ObjectId (24-char hex) rules out any "../" traversal
    # sequences before it's used to build a filesystem path.
    doc_obj_id = _validate_object_id(document_id)

    documents_collection = Database.get_documents_collection()
    doc = await documents_collection.find_one({"_id": doc_obj_id, "session_id": session_id})
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found."
        )

    file_path = os.path.join(UPLOAD_DIR, f"{document_id}.pdf")
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PDF file not found."
        )
    return FileResponse(file_path, media_type="application/pdf")


@router.get("/{document_id}/report")
async def get_document_report(document_id: str, session_id: str = Depends(get_session_id)):
    """
    Generates a beautifully formatted PDF compliance report and returns it as a download.
    Scoped to the caller's session via sid query param or X-Session-Id header.
    """
    doc_obj_id = _validate_object_id(document_id)
    try:
        documents_collection = Database.get_documents_collection()
        doc = await documents_collection.find_one({"_id": doc_obj_id, "session_id": session_id})
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found."
            )

        if not doc.get("has_audit") or not doc.get("audit_report"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This document does not have a processed compliance audit report."
            )
            
        # Compile PDF report using report service
        pdf_buffer = generate_audit_pdf(doc["audit_report"], doc["filename"])

        # Sanitize the filename before it goes into a response header (blocks header
        # injection via quotes/CR-LF/non-ASCII characters in the original upload name).
        safe_filename = re.sub(r'[^\w\-. ]', '_', doc['filename'])
        headers = {
            "Content-Disposition": f'attachment; filename="BREACH_Audit_{safe_filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition"
        }

        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers=headers
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error compiling audit report PDF: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate report PDF due to an internal error."
        )


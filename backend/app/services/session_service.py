import logging
import os
from bson import ObjectId
from bson.errors import InvalidId
from app.database import Database

logger = logging.getLogger("app.session_service")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads")


async def sweep_orphaned_uploads() -> int:
    """
    Deletes PDF files in the uploads directory whose matching document record
    no longer exists in MongoDB (already removed via the expires_at TTL index
    or an explicit delete). MongoDB's TTL sweep only reaches the database -
    this is what keeps local disk in sync with it.
    """
    if not os.path.isdir(UPLOAD_DIR):
        return 0

    candidate_ids = {}
    for filename in os.listdir(UPLOAD_DIR):
        if not filename.lower().endswith(".pdf"):
            continue
        stem = filename[:-4]
        try:
            candidate_ids[ObjectId(stem)] = filename
        except InvalidId:
            continue

    if not candidate_ids:
        return 0

    documents_collection = Database.get_documents_collection()
    cursor = documents_collection.find({"_id": {"$in": list(candidate_ids.keys())}}, {"_id": 1})
    existing_ids = {doc["_id"] async for doc in cursor}

    deleted_count = 0
    for doc_id, filename in candidate_ids.items():
        if doc_id in existing_ids:
            continue
        file_path = os.path.join(UPLOAD_DIR, filename)
        try:
            os.remove(file_path)
            deleted_count += 1
        except OSError as e:
            logger.warning(f"Failed to remove orphaned upload {filename}: {e}")

    if deleted_count:
        logger.info(f"Swept {deleted_count} orphaned upload file(s) with no matching document record.")

    return deleted_count

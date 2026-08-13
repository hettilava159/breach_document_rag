import asyncio
import logging
import math
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from bson import ObjectId
from app.config import settings
from app.database import Database

logger = logging.getLogger("app.vector_service")

_local_embedder = None

def get_local_embedder():
    """
    Lazily initializes the fastembed model on first use.

    threads=1 caps onnxruntime's internal thread pool - on Render's free tier
    (0.1 shared CPU), the default unbounded thread pool oversubscribes the CPU
    without buying any real parallelism, so this avoids that waste. It must be
    set before fastembed's first import, since onnxruntime reads OMP_NUM_THREADS
    at native-library init time.
    """
    global _local_embedder
    if _local_embedder is None:
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
        from fastembed import TextEmbedding
        # BAAI/bge-small-en-v1.5 has 384 dimensions and is extremely fast and accurate
        _local_embedder = TextEmbedding(threads=1)
    return _local_embedder

async def get_embeddings(texts: List[str], is_query: bool = False) -> List[List[float]]:
    """
    Generates embeddings for a list of texts using fastembed (BAAI/bge-small-en-v1.5).
    Runs in a thread pool so it never blocks the async event loop.
    """
    if not texts:
        return []

    try:
        embedder = get_local_embedder()
        def run_embedding():
            return [e.tolist() for e in embedder.embed(texts)]
        return await asyncio.to_thread(run_embedding)
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        raise RuntimeError(f"Embedding failed: {str(e)}")


async def store_document_chunks(
    document_id: str,
    chunks: List[Dict[str, Any]],
    session_id: str,
    expires_at: datetime,
    start_progress: int = 0
) -> int:
    """
    Generates embeddings in small batches and saves them in MongoDB.

    Batch size is a direct memory/request-count tradeoff: measured against the
    real model, a batch of 100 chunks holds ~28MB of intermediate embedding
    output in memory at once versus ~13MB for a batch of 25 - on Render's
    512MB free tier (where the model itself already takes ~215MB just to load),
    that difference is the gap between fitting and OOM-crashing on a large
    document. 25 trades a few more (cheap, local) round-trips to Mongo for a
    meaningfully lower peak.
    """
    if not chunks:
        return 0

    batch_size = 25
    total_stored = 0
    total_chunks = len(chunks)
    doc_obj_id = ObjectId(document_id)
    
    chunks_collection = Database.get_chunks_collection()
    documents_collection = Database.get_documents_collection()

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        
        # Build contextualized texts if context exists
        texts = []
        for chunk in batch:
            if chunk.get("context"):
                texts.append(f"{chunk['context']}\n\n{chunk['text']}")
            else:
                texts.append(chunk["text"])
        
        # Generate embeddings for this batch
        embeddings = await get_embeddings(texts, is_query=False)
        
        if len(embeddings) != len(batch):
            raise ValueError("Generated embeddings count does not match batch chunks count.")

        db_chunks = []
        for idx, chunk in enumerate(batch):
            db_chunks.append({
                "document_id": doc_obj_id,
                "session_id": session_id,
                "expires_at": expires_at,
                "text": chunk["text"],
                "context": chunk.get("context", ""),
                "embedding": embeddings[idx],
                "clause_reference": chunk.get("clause_reference", "N/A"),
                "category": chunk.get("category", "General"),
                "metadata": {
                    "page_number": chunk["metadata"]["page_number"],
                    "chunk_index": chunk["metadata"]["chunk_index"]
                }
            })
            
        await chunks_collection.insert_many(db_chunks)
        total_stored += len(db_chunks)
        
        # Calculate progress from the remaining percentage space
        if start_progress == 80:
            progress = 80 + int((total_stored / total_chunks) * 20)
        else:
            progress = int((total_stored / total_chunks) * 100)
            
        progress = min(progress, 99)
        
        await documents_collection.update_one(
            {"_id": doc_obj_id},
            {"$set": {"processing_progress": progress}}
        )
        
        logger.info(f"Stored batch {i // batch_size + 1}: {total_stored}/{total_chunks} chunks with context for document {document_id}.")

    return total_stored


def calculate_cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """
    Mathematical helper to compute cosine similarity between two vectors.
    Used as a fallback for local MongoDB setups.
    """
    dot_product = sum(a * b for a, b in zip(v1, v2))
    magnitude_v1 = math.sqrt(sum(a * a for a in v1))
    magnitude_v2 = math.sqrt(sum(b * b for b in v2))
    
    if not magnitude_v1 or not magnitude_v2:
        return 0.0
    return dot_product / (magnitude_v1 * magnitude_v2)


async def search_vector_chunks(
    query_vector: List[float],
    limit: int = 20,
    document_id: str = None,
    session_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves candidate chunks using Vector Search.
    Tries Atlas Vector Search first, falling back to local cosine similarity calculation if Atlas fails.
    Always scoped to session_id so one session's chunks are never returned to another.
    """
    chunks_collection = Database.get_chunks_collection()
    is_atlas = settings.MONGODB_URI.startswith("mongodb+srv://")

    if is_atlas:
        try:
            atlas_filter = {}
            if document_id:
                atlas_filter["document_id"] = ObjectId(document_id)
            if session_id:
                atlas_filter["session_id"] = session_id

            vector_search_stage = {
                "index": settings.ATLAS_VECTOR_INDEX,
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": 100,
                "limit": limit
            }
            if atlas_filter:
                vector_search_stage["filter"] = atlas_filter

            pipeline = [
                {"$vectorSearch": vector_search_stage},
                {
                    "$project": {
                        "document_id": 1,
                        "text": 1,
                        "context": 1,
                        "metadata": 1,
                        "score": {"$meta": "vectorSearchScore"}
                    }
                }
            ]

            results = []
            cursor = chunks_collection.aggregate(pipeline)
            async for doc in cursor:
                results.append({
                    "document_id": str(doc["document_id"]),
                    "text": doc["text"],
                    "context": doc.get("context", ""),
                    "page_number": doc["metadata"]["page_number"],
                    "score": doc.get("score", 1.0)
                })

            if results:
                logger.info(f"Atlas Vector Search returned {len(results)} matches.")
                return results

        except Exception as atlas_err:
            logger.warning(f"MongoDB Atlas Vector Search failed, falling back to local cosine similarity: {atlas_err}")

    # Local Fallback
    logger.info("Performing local in-memory cosine similarity search...")
    filter_query = {}
    if document_id:
        filter_query["document_id"] = ObjectId(document_id)
    if session_id:
        filter_query["session_id"] = session_id

    candidates = []
    cursor = chunks_collection.find(filter_query, {"text": 1, "context": 1, "embedding": 1, "metadata": 1, "document_id": 1})
    async for doc in cursor:
        candidates.append(doc)

    if not candidates:
        return []

    scored_results = []
    for doc in candidates:
        if "embedding" not in doc or not doc["embedding"]:
            continue
        similarity = calculate_cosine_similarity(query_vector, doc["embedding"])
        scored_results.append({
            "document_id": str(doc["document_id"]),
            "text": doc["text"],
            "context": doc.get("context", ""),
            "page_number": doc["metadata"]["page_number"],
            "score": similarity
        })

    scored_results.sort(key=lambda x: x["score"], reverse=True)
    return scored_results[:limit]


async def search_keyword_chunks(
    query_text: str,
    limit: int = 20,
    document_id: str = None,
    session_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves candidate chunks using Keyword/Lexical Search.
    Tries Atlas Search first. Falls back to standard MongoDB $text search, then regex term matching.
    Always scoped to session_id so one session's chunks are never returned to another.
    """
    chunks_collection = Database.get_chunks_collection()
    is_atlas = settings.MONGODB_URI.startswith("mongodb+srv://")

    # 1. Try MongoDB Atlas Search ($search)
    if is_atlas:
        try:
            search_clause = {
                "text": {
                    "query": query_text,
                    "path": ["text", "context"]
                }
            }

            filter_clauses = []
            if document_id:
                filter_clauses.append({"equals": {"value": ObjectId(document_id), "path": "document_id"}})
            if session_id:
                filter_clauses.append({"equals": {"value": session_id, "path": "session_id"}})

            if filter_clauses:
                search_stage = {
                    "index": settings.ATLAS_SEARCH_INDEX,
                    "compound": {
                        "must": [search_clause],
                        "filter": filter_clauses
                    }
                }
            else:
                search_stage = {
                    "index": settings.ATLAS_SEARCH_INDEX,
                    "text": {
                        "query": query_text,
                        "path": ["text", "context"]
                    }
                }

            pipeline = [
                {"$search": search_stage},
                {
                    "$project": {
                        "document_id": 1,
                        "text": 1,
                        "context": 1,
                        "metadata": 1,
                        "score": {"$meta": "searchScore"}
                    }
                },
                {"$limit": limit}
            ]

            results = []
            try:
                cursor = chunks_collection.aggregate(pipeline)
                async for doc in cursor:
                    results.append({
                        "document_id": str(doc["document_id"]),
                        "text": doc["text"],
                        "context": doc.get("context", ""),
                        "page_number": doc["metadata"]["page_number"],
                        "score": doc.get("score", 1.0)
                    })
            except Exception as compound_err:
                logger.warning(f"Atlas Search with compound query failed, trying search with post-match filter: {compound_err}")
                # Secondary fallback: $search without compound, then $match document_id
                search_stage_simple = {
                    "index": settings.ATLAS_SEARCH_INDEX,
                    "text": {
                        "query": query_text,
                        "path": ["text", "context"]
                    }
                }
                pipeline_fallback = [
                    {"$search": search_stage_simple}
                ]
                match_filter = {}
                if document_id:
                    match_filter["document_id"] = ObjectId(document_id)
                if session_id:
                    match_filter["session_id"] = session_id
                if match_filter:
                    pipeline_fallback.append({"$match": match_filter})


                pipeline_fallback.extend([
                    {
                        "$project": {
                            "document_id": 1,
                            "text": 1,
                            "context": 1,
                            "metadata": 1,
                            "score": {"$meta": "searchScore"}
                        }
                    },
                    {"$limit": limit}
                ])
                
                results = []
                cursor = chunks_collection.aggregate(pipeline_fallback)
                async for doc in cursor:
                    results.append({
                        "document_id": str(doc["document_id"]),
                        "text": doc["text"],
                        "context": doc.get("context", ""),
                        "page_number": doc["metadata"]["page_number"],
                        "score": doc.get("score", 1.0)
                    })

            if results:
                logger.info(f"Atlas Search returned {len(results)} matches.")
                return results

        except Exception as atlas_search_err:
            logger.warning(f"MongoDB Atlas Search ($search) failed, falling back to standard text index: {atlas_search_err}")

    # 2. Traditional MongoDB Text Index search ($text) fallback
    try:
        query_filter = {}
        if document_id:
            query_filter["document_id"] = ObjectId(document_id)
        if session_id:
            query_filter["session_id"] = session_id
        query_filter["$text"] = {"$search": query_text}

        cursor = chunks_collection.find(
            query_filter,
            {
                "document_id": 1,
                "text": 1,
                "context": 1,
                "metadata": 1,
                "score": {"$meta": "textScore"}
            }
        ).sort([("score", {"$meta": "textScore"})]).limit(limit)

        results = []
        async for doc in cursor:
            results.append({
                "document_id": str(doc["document_id"]),
                "text": doc["text"],
                "context": doc.get("context", ""),
                "page_number": doc["metadata"]["page_number"],
                "score": doc.get("score", 1.0)
            })

        if results:
            logger.info(f"MongoDB Fallback Text Index Search returned {len(results)} matches.")
            return results

    except Exception as text_index_err:
        logger.warning(f"MongoDB fallback text search index query failed, falling back to regex matching: {text_index_err}")

    # 3. Python-based regex search fallback
    try:
        words = [w for w in query_text.strip().split() if len(w) > 2]
        if not words:
            words = [query_text.strip()]

        # Escape each term so user input can never be interpreted as regex syntax
        # (unescaped input here was a ReDoS vector via pathological quantifier patterns).
        regex_pattern = "|".join(re.escape(w) for w in words)
        query_filter = {}
        if document_id:
            query_filter["document_id"] = ObjectId(document_id)
        if session_id:
            query_filter["session_id"] = session_id

        query_filter["$or"] = [
            {"text": {"$regex": regex_pattern, "$options": "i"}},
            {"context": {"$regex": regex_pattern, "$options": "i"}}
        ]

        cursor = chunks_collection.find(
            query_filter,
            {"document_id": 1, "text": 1, "context": 1, "metadata": 1}
        ).limit(limit)

        results = []
        async for doc in cursor:
            text_lower = doc["text"].lower()
            context_lower = doc.get("context", "").lower()
            score = 0.0
            for word in words:
                w_lower = word.lower()
                if w_lower in text_lower:
                    score += 1.0
                if w_lower in context_lower:
                    score += 0.5
            
            results.append({
                "document_id": str(doc["document_id"]),
                "text": doc["text"],
                "context": doc.get("context", ""),
                "page_number": doc["metadata"]["page_number"],
                "score": score
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        logger.info(f"Regex Keyword Search fallback returned {len(results)} matches.")
        return results
    except Exception as regex_err:
        logger.error(f"Regex Keyword Search fallback failed: {regex_err}")
        return []


def reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]], 
    keyword_results: List[Dict[str, Any]], 
    k: int = 60
) -> List[Dict[str, Any]]:
    """
    Combines vector and keyword search results using Reciprocal Rank Fusion (RRF).
    """
    rrf_scores = {}
    doc_map = {}

    def get_chunk_key(chunk):
        return f"{chunk['document_id']}_{chunk['page_number']}_{chunk['text'][:100]}"

    for rank, chunk in enumerate(vector_results):
        key = get_chunk_key(chunk)
        doc_map[key] = chunk
        rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (k + rank + 1))

    for rank, chunk in enumerate(keyword_results):
        key = get_chunk_key(chunk)
        if key not in doc_map:
            doc_map[key] = chunk
        rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (k + rank + 1))

    sorted_keys = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

    combined = []
    for key in sorted_keys:
        chunk = doc_map[key].copy()
        chunk["score"] = rrf_scores[key]
        combined.append(chunk)

    return combined


async def search_similar_chunks(
    query_text: str,
    limit: int = 4,
    document_id: str = None,
    session_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Performs a hybrid search combining Vector Search and Atlas Search (keyword)
    using Reciprocal Rank Fusion (RRF). Always scoped to session_id.
    """
    # 1. Generate query embedding
    query_embeddings = await get_embeddings([query_text], is_query=True)
    if not query_embeddings:
        return []
    query_vector = query_embeddings[0]

    # Retrieve slightly more candidates for better RRF combination
    candidate_limit = max(limit * 2, 40)

    # 2. Run searches in parallel
    vector_task = search_vector_chunks(query_vector, limit=candidate_limit, document_id=document_id, session_id=session_id)
    keyword_task = search_keyword_chunks(query_text, limit=candidate_limit, document_id=document_id, session_id=session_id)

    vector_results, keyword_results = await asyncio.gather(vector_task, keyword_task)

    # 3. Fuse results using Reciprocal Rank Fusion (RRF)
    hybrid_results = reciprocal_rank_fusion(vector_results, keyword_results)

    # Return top results up to the limit
    return hybrid_results[:limit]


async def wait_for_atlas_indexing(document_id: str, timeout_seconds: int = 6) -> bool:
    """
    Waits for MongoDB Atlas Vector index to fully index all chunks of a document.
    Queries the Atlas Vector Search index repeatedly until the indexed count matches 
    the total chunks count in the database, or times out.
    """
    is_atlas = settings.MONGODB_URI.startswith("mongodb+srv://")
    if not is_atlas:
        return True

    chunks_collection = Database.get_chunks_collection()
    
    # 1. Count the actual chunks stored in MongoDB synchronously
    total_stored = await chunks_collection.count_documents({"document_id": ObjectId(document_id)})
    if total_stored == 0:
        return True

    # 2. Get embedding dimensions dynamically from first chunk
    sample_chunk = await chunks_collection.find_one({"document_id": ObjectId(document_id)})
    dimensions = 384
    if sample_chunk and "embedding" in sample_chunk:
        dimensions = len(sample_chunk["embedding"])

    dummy_vector = [0.0] * dimensions
    
    logger.info(f"Atlas connection detected. Waiting for Atlas Vector Index to index all {total_stored} chunks...")
    
    start_time = asyncio.get_event_loop().time()
    poll_interval = 1.0
    
    while (asyncio.get_event_loop().time() - start_time) < timeout_seconds:
        pipeline = [
            {
                "$vectorSearch": {
                    "index": settings.ATLAS_VECTOR_INDEX,
                    "path": "embedding",
                    "queryVector": dummy_vector,
                    "numCandidates": max(100, total_stored * 2),
                    "limit": max(100, total_stored * 2),
                    "filter": {"document_id": ObjectId(document_id)}
                }
            },
            {
                "$count": "count"
            }
        ]
        try:
            indexed_count = 0
            cursor = chunks_collection.aggregate(pipeline)
            async for doc in cursor:
                indexed_count = doc.get("count", 0)
            
            logger.info(f"Atlas Search Index progress for doc {document_id}: {indexed_count}/{total_stored} chunks indexed.")
            
            if indexed_count >= total_stored:
                logger.info(f"Atlas Search Index is fully synchronized for document {document_id}.")
                return True
        except Exception as e:
            # If the index is not found or is still building, it might throw an error.
            # We catch it and log/wait.
            logger.warning(f"Error checking Atlas Vector index count: {e}. Index may still be building.")
            
        await asyncio.sleep(poll_interval)
        
    logger.warning(f"Timeout waiting for Atlas Search Index synchronization for document {document_id}. Proceeding anyway.")
    return False



import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.config import settings
from app.database import init_db, Database
from app.rate_limiter import limiter
from app.routes import document, query

# Set up logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("app.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles application startup and shutdown lifecycle events.
    Initializes database connection and indexes, and closes connections cleanly.
    """
    logger.info("Initializing database and search indexes...")
    try:
        await init_db()
        logger.info("Database initialized successfully.")

        # Purge collections on startup if configured (for clean dev testing)
        if settings.CLEAR_DB_ON_STARTUP:
            logger.info("CLEAR_DB_ON_STARTUP is enabled. Purging collections...")
            await Database.get_documents_collection().delete_many({})
            await Database.get_chunks_collection().delete_many({})
            logger.info("Database collections purged successfully on startup.")

    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")
        # Fail fast: an app that can't reach its database shouldn't boot and
        # silently report itself healthy on every subsequent request.
        raise

    yield
    
    logger.info("Shutting down database connections...")
    await Database.disconnect()
    logger.info("Application shutdown complete.")


app = FastAPI(
    title="BREACH API",
    description="BREACH — automated contract risk auditor & Q&A agent. Production-grade Document Retrieval-Augmented Generation (RAG) API using FastAPI and MongoDB Atlas Vector Search.",
    version="1.0.0",
    lifespan=lifespan
)

# Rate limiting (protects paid LLM/Tavily calls behind upload & query from abuse)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Enable CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(document.router, prefix="/api")
app.include_router(query.router, prefix="/api")

@app.get("/health", tags=["health"])
async def health_check():
    """
    Health check endpoint for container environments and cloud platforms.
    Actually verifies the database connection instead of always reporting healthy.
    """
    try:
        await Database.get_db().command("ping")
        db_status = "connected"
    except Exception as e:
        logger.error(f"Health check DB ping failed: {e}")
        db_status = "disconnected"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "environment": settings.ENV,
        "llm_provider": settings.LLM_PROVIDER,
        "database": db_status
    }

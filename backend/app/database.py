import logging
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure
from app.config import settings

logger = logging.getLogger("app.database")

class Database:
    client: AsyncIOMotorClient = None
    db = None

    @classmethod
    async def connect(cls):
        """Establish connection to MongoDB."""
        if cls.client is not None:
            return
        
        try:
            # Async client using the connection string from settings
            cls.client = AsyncIOMotorClient(settings.MONGODB_URI)
            cls.db = cls.client[settings.DB_NAME]
            
            # Await the ping command to verify the connection is active
            await cls.db.command("ping")
            logger.info("Successfully connected to MongoDB!")
        except ConnectionFailure as e:
            logger.critical(f"Could not connect to MongoDB: {e}")
            raise e

    @classmethod
    async def disconnect(cls):
        """Close connection to MongoDB."""
        if cls.client is not None:
            cls.client.close()
            cls.client = None
            cls.db = None
            logger.info("Closed MongoDB connection.")

    @classmethod
    def get_db(cls):
        """Get the database instance."""
        if cls.db is None:
            raise RuntimeError("Database not initialized. Call connect() first.")
        return cls.db

    @classmethod
    def get_documents_collection(cls):
        return cls.get_db()["documents"]

    @classmethod
    def get_chunks_collection(cls):
        return cls.get_db()["chunks"]

async def init_db():
    """Helper to initialize connections and create standard indexes."""
    await Database.connect()

    # Create indexes to speed up typical operations
    # Index documents by upload date to allow fast sorting when listing files
    documents_collection = Database.get_documents_collection()
    await documents_collection.create_index("uploaded_at")
    await documents_collection.create_index("session_id")

    # TTL index: MongoDB's background process auto-deletes a document once its
    # expires_at timestamp passes. This is what enforces the 5-hour session cap
    # without any custom scheduler.
    await documents_collection.create_index("expires_at", expireAfterSeconds=0)

    # Index chunks by document_id so we can quickly retrieve/delete all chunks of a document
    chunks_collection = Database.get_chunks_collection()
    await chunks_collection.create_index("document_id")
    await chunks_collection.create_index("session_id")
    await chunks_collection.create_index("expires_at", expireAfterSeconds=0)

    # Standard text index on text/context fields for local keyword search fallback
    try:
        await chunks_collection.create_index([("text", "text"), ("context", "text")], name="fallback_text_index")
        logger.info("Fallback text search index initialized successfully.")
    except Exception as e:
        logger.warning(f"Could not initialize fallback text search index: {e}")

    logger.info("Database collections and standard indexes initialized successfully!")

import uvicorn
from app.config import settings

if __name__ == "__main__":
    print(f"Launching Uvicorn server on http://localhost:{settings.PORT}...")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.PORT,
        reload=settings.ENV == "development"
    )

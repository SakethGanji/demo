import uvicorn
from app.main import app
from app.infra.config import settings

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)

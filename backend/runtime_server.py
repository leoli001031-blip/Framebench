import os

import uvicorn
from backend.main import app


def main():
    host = os.getenv("FRAMEBENCH_BACKEND_HOST") or os.getenv("FILM_MASTER_BACKEND_HOST") or "127.0.0.1"
    port = int(os.getenv("FRAMEBENCH_BACKEND_PORT") or os.getenv("FILM_MASTER_BACKEND_PORT") or "8000")
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=os.getenv("FRAMEBENCH_LOG_LEVEL") or os.getenv("FILM_MASTER_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()

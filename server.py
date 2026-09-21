import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.server:app",
        host="0.0.0.0",
        port=8000,
        reload=os.getenv("UVICORN_RELOAD") == "1",
    )


if __name__ == "__main__":
    main()
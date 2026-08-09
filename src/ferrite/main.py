"""Entry point for the Ferrite API server.""""

import uvicorn

from ferrite.config import get_settings


def main():
    settings = get_settings()
    uvicorn.run(
        "ferrite.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()

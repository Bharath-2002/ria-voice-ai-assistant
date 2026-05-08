"""Application entry point."""

import uvicorn


def run() -> None:
    """Entry point for `uv run ria`."""
    uvicorn.run(
        "app.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    run()

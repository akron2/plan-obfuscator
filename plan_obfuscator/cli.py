from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .config import Settings
from .web import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Plan Obfuscator locally")
    parser.add_argument("--port", type=int, default=8765, help="Local HTTP port")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument("--data-dir", type=Path, help="Directory for the SQLite database")
    parser.add_argument(
        "--proxy",
        help=(
            "Proxy URL used by run.bat/run.ps1 only when runtime dependencies "
            "must be installed"
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings(
        data_dir=args.data_dir,
        port=args.port,
        open_browser=not args.no_browser,
    )
    app = create_app(settings)
    url = f"http://{settings.host}:{settings.port}"
    if settings.open_browser:
        timer = threading.Timer(0.8, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()

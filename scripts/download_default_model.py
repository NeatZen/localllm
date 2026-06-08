#!/usr/bin/env python3
"""Download the default bundled GGUF model for turnkey local AI."""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from services.bundled_llm import (  # noqa: E402
    DEFAULT_MODEL_FILE,
    DEFAULT_MODEL_REPO,
    download_model_sync,
    is_model_downloaded,
    model_path,
)


def main() -> int:
    print("NeatAi — bundled local AI model download")
    print(f"  Repo: {DEFAULT_MODEL_REPO}")
    print(f"  File: {DEFAULT_MODEL_FILE}")
    print(f"  Size: ~2 GB (one-time download)")
    print()

    if is_model_downloaded():
        print(f"Already present: {model_path()}")
        return 0

    try:
        path = download_model_sync()
        print(f"Downloaded: {path}")
        return 0
    except Exception as e:
        print(f"Download failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

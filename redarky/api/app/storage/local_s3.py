"""
app/storage/local_s3.py

Local filesystem storage for raw JSON dumps (debugging / backup).
Most data lives in PostgreSQL — this is just a recovery / audit layer.

Layout mirrors S3 conventions:
  {base_path}/{layer}/{project_id}/{date}/{uuid}.json

Layers:
  raw          — raw items as received from the Go scraper
  processed    — items after Stage-1 keyword matching
  dead-letter  — items that failed validation (for replay)
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import settings

S3_LAYERS = ("raw", "processed", "dead-letter")


class LocalS3Storage:
    def __init__(self, base_path: str | None = None) -> None:
        self.base_path = Path(base_path or settings.LOCAL_S3_BASE_PATH).resolve()
        for layer in S3_LAYERS:
            (self.base_path / layer).mkdir(parents=True, exist_ok=True)

    def write_json(self, layer: str, mission_id: str, payload) -> str:
        """Writes payload as JSON under {layer}/{mission_id}/{date}/{uuid}.json"""
        if layer not in S3_LAYERS:
            raise ValueError(f"Unsupported layer: {layer!r} (allowed: {S3_LAYERS})")

        date_partition = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_name = f"{uuid4()}.json"
        file_path = self.base_path / layer / mission_id / date_partition / file_name
        file_path.parent.mkdir(parents=True, exist_ok=True)

        file_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return str(file_path)

    def read_json(self, path: str):
        """Reads JSON from a path returned by write_json."""
        return json.loads(Path(path).read_text(encoding="utf-8"))

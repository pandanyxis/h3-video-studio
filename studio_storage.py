"""Durable files shared by Studio's web service and production worker."""
import json
import os
from pathlib import Path
import uuid


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def progress(project, stage, **details):
    atomic_json(Path(project) / "progress.json", {"stage": stage, **details})

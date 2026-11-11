"""Durable files shared by Studio's web service and production worker."""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import time
import uuid


class ProductionCancelled(Exception):
    """The user stopped this Studio production."""


def check_cancelled(project):
    if (Path(project) / "cancel.request").is_file():
        raise ProductionCancelled()


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


@contextmanager
def gpu_lock():
    """Keep the local worker and CLI from using the same GPU at the same time."""
    if os.name == "nt":
        import msvcrt

        with (Path.home() / ".h3-studio-render.lock").open("a+b") as lock:
            lock.seek(0)
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(1)
            try:
                yield
            finally:
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        with Path("/workspace/.studio-render.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

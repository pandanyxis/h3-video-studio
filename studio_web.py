"""Private web interface and persistent production queue for H3 Video Studio."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid
import warnings

from PIL import Image, UnidentifiedImageError

from prepare_timeline import ASPECT_SIZES, FPS
from studio_profiles import SELECTION_PREFIX, default_settings, selection_profile
from studio_render import RenderClient, RenderError
from studio_storage import atomic_json, progress, read_json
from studio_writer import DEFAULT_WRITER_PROMPT, MAX_WRITER_PROMPT

ROOT = Path(os.environ.get("STUDIO_PROJECTS", "/workspace/studio/projects"))
HERE = Path(__file__).resolve().parent
QUEUE = ThreadPoolExecutor(max_workers=1)
ACTIVE = set()
QUEUE_LOCK = threading.Lock()
MAX_IMAGE = 8 * 1024 * 1024
MAX_REQUEST = 100 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000
DEFAULT_PROFILE = {"id": "studio-turbo8", "title": "Studio H3 Turbo 8-step"}


def render_profiles():
    try:
        profiles = RenderClient().capabilities()["profiles"]
    except (OSError, ValueError, RenderError):
        profiles = []
    return [DEFAULT_PROFILE, *(
        {"id": item["id"], "title": item.get("title", item["id"])}
        for item in profiles if item.get("ready") and item["id"] != DEFAULT_PROFILE["id"]
        and not item["id"].startswith(SELECTION_PREFIX)
    )]


def stage(payload):
    story, images = payload.get("story"), payload.get("images")
    writer_prompt = payload.get("writer_prompt", "")
    if not isinstance(writer_prompt, str) or len(writer_prompt) > MAX_WRITER_PROMPT:
        raise ValueError(f"Gebruik maximaal {MAX_WRITER_PROMPT} tekens voor de Writer-instructies.")
    profile = payload.get("profile", DEFAULT_PROFILE["id"])
    settings = payload.get("render_settings")
    aspect = payload.get("aspect", "auto")
    if not isinstance(aspect, str) or (aspect != "auto" and aspect not in ASPECT_SIZES):
        raise ValueError("Kies automatisch, 16:9 of 9:16.")
    if settings is None and profile != DEFAULT_PROFILE["id"] and not any(
            profile == item["id"] for item in render_profiles()):
        raise ValueError("Dit renderprofiel is niet beschikbaar.")
    if not isinstance(story, str) or not story.strip() or len(story) > 20_000:
        raise ValueError("Voer een shotidee of verhaal in van maximaal 20.000 tekens.")
    if not isinstance(images, list) or not 1 <= len(images) <= 9:
        raise ValueError("Selecteer één tot negen referentiebeelden.")
    project = ROOT / uuid.uuid4().hex
    project.mkdir(parents=True)
    try:
        (project / "story.txt").write_text(story.strip(), encoding="utf-8")
        (project / "writer-prompt.txt").write_text(writer_prompt.strip() or DEFAULT_WRITER_PROMPT, encoding="utf-8")
        for index, encoded in enumerate(images, 1):
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE + 2) // 3):
                raise ValueError("Een referentiebeeld mag maximaal 8 MiB zijn.")
            data = base64.b64decode(encoded, validate=True)
            if not data or len(data) > MAX_IMAGE:
                raise ValueError("Een referentiebeeld is leeg of groter dan 8 MiB.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get(image.format)
                    if not extension:
                        raise ValueError("Gebruik JPEG-, PNG- of WebP-beelden.")
                    image.verify()
            if index == 1 and aspect == "auto":
                with Image.open(BytesIO(data)) as image:
                    width, height = image.size
                    if image.getexif().get(274) in (5, 6, 7, 8):
                        width, height = height, width
                    aspect = "9:16" if height > width else "16:9"
            (project / f"reference-{index:02d}.{extension}").write_bytes(data)
        if settings is not None:
            try:
                options = RenderClient().model_options()
            except (OSError, ValueError, RenderError):
                raise ValueError("ComfyUI kan de geïnstalleerde modellen nog niet ophalen.") from None
            profile = selection_profile(settings, options)
        atomic_json(project / "project.json", {
            "title": story.strip().splitlines()[0][:80],
            "created": datetime.now(timezone.utc).isoformat(),
            "profile": profile,
            "aspect": aspect,
        })
        set_status(project, "queued")
        progress(project, "queued")
    except Exception:
        shutil.rmtree(project)
        raise
    return project


def set_status(project, status):
    temporary = project / ("status." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(status, encoding="utf-8")
    temporary.replace(project / "status")


def project_data(project, details=False):
    status = "done" if (project / "film.mp4").is_file() else (project / "status").read_text(encoding="utf-8")
    meta = read_json(project / "project.json") if (project / "project.json").is_file() else {}
    state = read_json(project / "progress.json") if (project / "progress.json").is_file() else {"stage": status}
    result = {**meta, "id": project.name, "status": status, "progress": state}
    if details:
        result["story"] = (project / "story.txt").read_text(encoding="utf-8")
        result["references"] = [path.name for path in sorted(project.glob("reference-*"))]
        result["clips"] = [path.name for path in sorted((project / "clips").glob("shot-*.mp4"))]
        shot_file = project / "shots.json"
        result["shots"] = read_json(shot_file)["shots"] if shot_file.is_file() else []
        timeline = project / "timeline.json"
        if timeline.is_file():
            boxes = read_json(timeline)["timeline"]["boxes"]
            for shot, box in zip(result["shots"], boxes):
                shot["duration_seconds"] = box["frames"] / FPS
    return result


def render(project, resume=False):
    try:
        set_status(project, "running")
        command = [sys.executable, str(HERE / "run_studio.py"), str(project / "story.txt"),
                   str(project), *map(str, sorted(project.glob("reference-*")))]
        meta = project_data(project)
        if meta.get("profile"):
            command.extend(["--profile", meta["profile"]])
        if meta.get("aspect"):
            command.extend(["--aspect", meta["aspect"]])
        if resume:
            command.append("--resume")
        with (project / "render.log").open("ab") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        if not (project / "film.mp4").is_file():
            raise RuntimeError("De productie eindigde zonder videobestand.")
        set_status(project, "done")
    except Exception as error:
        state_file = project / "progress.json"
        state = read_json(state_file) if state_file.is_file() else {}
        if state.get("stage") != "failed":
            progress(project, "failed", error="De productie is onderbroken. Controleer het productielog en hervat het project.")
        set_status(project, "failed")
        print(f"Studio project {project.name}: {error}", file=sys.stderr, flush=True)
    finally:
        with QUEUE_LOCK:
            ACTIVE.discard(project.name)


def schedule(project, resume=False):
    with QUEUE_LOCK:
        if project.name in ACTIVE or (project / "film.mp4").is_file():
            return False
        ACTIVE.add(project.name)
        set_status(project, "queued")
        progress(project, "queued")
        QUEUE.submit(render, project, resume)
    return True


class StudioHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(120)

    def reply(self, code, kind, data, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def json(self, code, **data):
        self.reply(code, "application/json", json.dumps(data).encode())

    def local_host(self):
        return re.fullmatch(r"(localhost|127\.0\.0\.1)(:\d+)?", self.headers.get("Host", ""))

    def allowed(self):
        if not getattr(self.server, "public_origin", None):
            if self.local_host():
                return True
            self.json(403, error="Open Studio via localhost.")
            return False
        expected = self.server.studio_auth
        if not expected:
            self.reply(503, "text/plain; charset=utf-8",
                       "Stel STUDIO_PASSWORD in bij de Runpod-omgeving en start de Pod opnieuw.".encode())
            return False
        supplied = self.headers.get("Authorization", "").encode()
        if hmac.compare_digest(supplied, expected):
            return True
        self.reply(401, "text/plain; charset=utf-8", "Log in als studio.".encode(), {
            "WWW-Authenticate": 'Basic realm="H3 Video Studio", charset="UTF-8"',
        })
        return False

    def serve_file(self, path):
        kinds = {".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".json": "application/json", ".log": "text/plain; charset=utf-8"}
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            start, end, code = 0, size - 1, 200
            requested = self.headers.get("Range")
            if requested:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                if not match or not any(match.groups()):
                    return self.json(416, error="Ongeldig bereik.")
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]), end) if match[2] else end
                else:
                    start = max(0, size - int(match[2]))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", kinds[path.suffix])
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            stream.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        if self.path == "/health":
            return self.json(200, status="ok")
        if not self.allowed():
            return
        if self.path == "/profiles":
            return self.json(200, profiles=render_profiles())
        if self.path == "/writer-prompt":
            return self.json(200, prompt=DEFAULT_WRITER_PROMPT, max_length=MAX_WRITER_PROMPT)
        if self.path == "/render-options":
            try:
                options = RenderClient().model_options()
                defaults = default_settings()
            except (OSError, ValueError, RenderError):
                return self.json(503, error="ComfyUI is nog niet klaar. Probeer de modellen opnieuw te laden.")
            return self.json(200, **options, defaults=defaults)
        if self.path == "/":
            return self.reply(200, "text/html; charset=utf-8", (HERE / "studio.html").read_bytes())
        if self.path == "/projects":
            projects = [project_data(path.parent) for path in ROOT.glob("*/status")]
            return self.json(200, projects=sorted(projects, key=lambda item: item.get("created", ""), reverse=True))
        match = re.fullmatch(r"/projects/([a-f0-9]{32})(?:/(film\.mp4|shots\.json|render\.log|reference-\d{2}\.(?:png|jpg|webp)|clips/shot-\d{2}\.mp4))?", self.path)
        if not match:
            return self.json(404, error="Project niet gevonden.")
        project = ROOT / match[1]
        if not (project / "status").is_file():
            return self.json(404, error="Project niet gevonden.")
        if match[2]:
            path = project / match[2]
            if not path.is_file():
                return self.json(404, error="Bestand is nog niet beschikbaar.")
            return self.serve_file(path)
        self.json(200, **project_data(project, details=True))

    def do_POST(self):
        if not self.allowed():
            return
        origin = getattr(self.server, "public_origin", None) or f"http://{self.headers.get('Host')}"
        if (self.headers.get("Origin") != origin
                or self.headers.get_content_type() != "application/json"):
            return self.json(403, error="Open Studio vanaf het Studio-adres.")
        retry = re.fullmatch(r"/projects/([a-f0-9]{32})/resume", self.path)
        if retry:
            project = ROOT / retry[1]
            if not (project / "status").is_file():
                return self.json(404, error="Project niet gevonden.")
            if not schedule(project, resume=True):
                return self.json(409, error="Dit project wordt al verwerkt of is voltooid.")
            return self.json(202, id=project.name)
        if self.path != "/projects":
            return self.json(404, error="Onbekend adres.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST:
                return self.json(413, error="De upload is te groot of leeg.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Ongeldig project.")
            project = stage(payload)
        except (ValueError, TypeError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            return self.json(400, error=str(error) if isinstance(error, ValueError) else "Het referentiebeeld kon niet worden gelezen. Gebruik JPEG, PNG of WebP.")
        schedule(project)
        self.json(202, id=project.name)


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", type=Path, default=ROOT)
    parser.add_argument("--port", type=int, default=8190)
    args = parser.parse_args()
    ROOT = args.projects.resolve()
    ROOT.mkdir(parents=True, exist_ok=True)
    pod_id = os.environ.get("RUNPOD_POD_ID", "")
    if pod_id and not re.fullmatch(r"[a-z0-9]+", pod_id):
        raise SystemExit("Ongeldige RUNPOD_POD_ID.")
    server = ThreadingHTTPServer(("0.0.0.0" if pod_id else "127.0.0.1", args.port), StudioHandler)
    server.public_origin = f"https://{pod_id}-{args.port}.proxy.runpod.net" if pod_id else None
    password = os.environ.get("STUDIO_PASSWORD", "")
    server.studio_auth = None
    if len(password) >= 16 and "{{" not in password:
        server.studio_auth = b"Basic " + base64.b64encode(("studio:" + password).encode())
    for status_file in ROOT.glob("*/status"):
        if status_file.read_text(encoding="utf-8") in ("queued", "running"):
            schedule(status_file.parent)
    print(f"H3 Video Studio {server.public_origin or f'http://localhost:{args.port}'}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        QUEUE.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()

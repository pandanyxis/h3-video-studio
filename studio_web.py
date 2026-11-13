"""Private web interface and persistent production queue for H3 Video Studio."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import uuid
import warnings

from PIL import Image, UnidentifiedImageError

from prepare_timeline import ASPECT_SIZES, FPS
from studio_profiles import MAX_LORAS, SELECTION_PREFIX, default_settings, selection_profile
from studio_refine import (MAX_FEEDBACK, MAX_REVISED_PROMPT, clip_duration, refine_clip,
                           review_film, revision_inputs)
from studio_render import RenderClient, RenderError
from studio_remake import MAX_VIDEO, analyze, compile_plan
from studio_storage import atomic_json, gpu_lock, progress, read_json
from studio_writer import (DEFAULT_WRITER_PROMPT, MAX_REFERENCE_NOTE, MAX_WRITER_PROMPT,
                           bind_shot_references, qwen_ready, read_reference_notes, referenced_slots)

ROOT = Path(os.environ.get("STUDIO_PROJECTS", "/workspace/studio/projects"))
HERE = Path(__file__).resolve().parent
QUEUE = ThreadPoolExecutor(max_workers=1)
ACTIVE = set()
ACTIVE_REFINEMENTS = set()
QUEUE_LOCK = threading.Lock()
REMAKE_LOCK = threading.Lock()
REMAKE_ANALYSIS = {}
MAX_IMAGE = 8 * 1024 * 1024
MAX_REQUEST = 100 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000
DEFAULT_PROFILE = {"id": "studio-turbo8", "title": "Studio H3 Turbo 8-step"}


def startup_status():
    try:
        models = read_json(Path("/tmp/hf_download_status.json"))
    except (OSError, ValueError):
        models = {}
    try:
        planner_ready = qwen_ready()
    except OSError:
        planner_ready = False
    try:
        renderer_ready = any(
            profile["id"] == DEFAULT_PROFILE["id"] and profile.get("ready")
            for profile in RenderClient().capabilities()["profiles"]
        )
    except (OSError, ValueError, KeyError, RenderError):
        renderer_ready = False
    return {"planner_ready": planner_ready, "renderer_ready": renderer_ready,
            "models": [{"name": Path(name).name, "status": entry["status"]}
                       for name, entry in models.items()]}


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


def stage(payload, *, plan=None):
    story, images = payload.get("story"), payload.get("images")
    writer_prompt = payload.get("writer_prompt", "")
    if not isinstance(writer_prompt, str) or len(writer_prompt) > MAX_WRITER_PROMPT:
        raise ValueError(f"Use at most {MAX_WRITER_PROMPT} characters for the Writer instructions.")
    profile = payload.get("profile", DEFAULT_PROFILE["id"])
    settings = payload.get("render_settings")
    aspect = payload.get("aspect", "auto")
    if not isinstance(aspect, str) or (aspect != "auto" and aspect not in ASPECT_SIZES):
        raise ValueError("Choose automatic, 16:9, or 9:16.")
    if settings is None and profile != DEFAULT_PROFILE["id"] and not any(
            profile == item["id"] for item in render_profiles()):
        raise ValueError("This render profile is unavailable.")
    if not isinstance(story, str) or not story.strip() or len(story) > 20_000:
        raise ValueError("Enter a shot idea or story of up to 20,000 characters.")
    if not isinstance(images, list) or not (0 if plan else 1) <= len(images) <= 9:
        raise ValueError("Select one to nine reference images.")
    descriptions = payload.get("image_notes", [""] * len(images))
    if not isinstance(descriptions, list) or len(descriptions) != len(images) or any(
            not isinstance(note, str) or len(note) > MAX_REFERENCE_NOTE for note in descriptions):
        raise ValueError(f"Provide up to {MAX_REFERENCE_NOTE:,} characters of notes per reference image.")
    project = ROOT / uuid.uuid4().hex
    project.mkdir(parents=True)
    try:
        (project / "story.txt").write_text(story.strip(), encoding="utf-8")
        (project / "writer-prompt.txt").write_text(writer_prompt.strip() or DEFAULT_WRITER_PROMPT, encoding="utf-8")
        if plan:
            atomic_json(project / "shots.json", plan)
        notes = {}
        for index, (encoded, description) in enumerate(zip(images, descriptions), 1):
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE + 2) // 3):
                raise ValueError("Each reference image must be 8 MiB or smaller.")
            data = base64.b64decode(encoded, validate=True)
            if not data or len(data) > MAX_IMAGE:
                raise ValueError("A reference image is empty or exceeds 8 MiB.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get(image.format)
                    if not extension:
                        raise ValueError("Use JPEG, PNG, or WebP images.")
                    image.verify()
            if index == 1 and aspect == "auto":
                with Image.open(BytesIO(data)) as image:
                    width, height = image.size
                    if image.getexif().get(274) in (5, 6, 7, 8):
                        width, height = height, width
                    aspect = "9:16" if height > width else "16:9"
            filename = f"reference-{index:02d}.{extension}"
            (project / filename).write_bytes(data)
            if note := description.strip():
                notes[filename] = note
        if notes:
            atomic_json(project / "image-notes.json", notes)
        if settings is not None:
            try:
                options = RenderClient().model_options()
            except (OSError, ValueError, RenderError):
                raise ValueError("ComfyUI cannot list installed models yet.") from None
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


def stage_revision(parent, payload):
    approved = {}
    if "refinement_id" in payload:
        review_id = payload["refinement_id"]
        if not isinstance(review_id, str) or not re.fullmatch(r"[a-f0-9]{32}", review_id):
            raise ValueError("Choose a completed clip refinement.")
        review_path = parent / "refinements" / review_id / "review.json"
        if not review_path.is_file():
            raise ValueError("Clip refinement not found.")
        review = read_json(review_path)
        if review["status"] != "ready" or payload.get("shot") != review["shot"]:
            raise ValueError("Choose a completed refinement for this shot.")
        plan, index, feedback = revision_inputs(parent, review)
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_REVISED_PROMPT:
            raise ValueError(f"Enter a prompt of up to {MAX_REVISED_PROMPT:,} characters.")
        shot = plan["shots"][index]
        slots = {f"image{number}" for number, _ in enumerate(sorted(parent.glob("reference-*")), 17)}
        prompt = prompt.strip()
        bound = bind_shot_references(prompt, shot, slots, existing=referenced_slots(shot["prompt"]))
        if bound != prompt:
            missing = ", ".join(f"@{slot}" for slot in sorted(referenced_slots(bound) - referenced_slots(prompt)))
            raise ValueError(f"Keep the required image references in the approved prompt: {missing}.")
        approved = {"prompt": prompt, "refinement_id": review_id}
    else:
        _, index, feedback = revision_inputs(parent, payload)
    return save_revision(parent, f"Shot {index + 1} revised", {"revised_shot": index},
                         {"shot": index, "feedback": feedback, **approved})


def stage_film_revision(parent, payload):
    with QUEUE_LOCK:
        path = parent / "film-review" / "review.json"
        if not path.is_file():
            raise ValueError("Run a movie review first.")
        review = read_json(path)
        if review["status"] != "ready" or not review.get("id") or payload.get("review_id") != review["id"]:
            raise ValueError("Choose the current completed movie review.")
        prompts = {int(shot) - 1: item["prompt"] for shot, item in review.get("reviews", {}).items()
                   if item.get("prompt")}
        if not prompts:
            raise ValueError("This movie review has no changes to apply.")
        existing = ROOT / review["id"]
        if (existing / "status").is_file():
            return existing
        return save_revision(parent, "Film review revision", {"revised_shots": sorted(prompts)},
                             {"prompts": prompts}, project_id=review["id"])


def save_revision(parent, title_suffix, changes, revision, *, project_id=None):
    project = ROOT / (project_id or uuid.uuid4().hex)
    project.mkdir()
    try:
        for path in [parent / "story.txt", parent / "writer-prompt.txt", parent / "image-notes.json",
                     *sorted(parent.glob("reference-*"))]:
            if path.is_file():
                shutil.copyfile(path, project / path.name)
        meta = read_json(parent / "project.json")
        meta.pop("revised_shot", None)
        meta.pop("revised_shots", None)
        title = (project / "story.txt").read_text(encoding="utf-8").splitlines()[0][:80]
        atomic_json(project / "project.json", {
            **meta, "title": f"{title} · {title_suffix}",
            "created": datetime.now(timezone.utc).isoformat(), "parent": parent.name, **changes,
        })
        atomic_json(project / "revision.json", {"parent": parent.name, **revision})
        set_status(project, "queued")
        progress(project, "queued")
    except Exception:
        shutil.rmtree(project)
        raise
    return project


def run_refinement(project, directory):
    file = directory / "review.json"
    review = {}
    try:
        review = read_json(file)
        atomic_json(file, {**review, "status": "reviewing"})
        with gpu_lock():
            if directory.name == "film-review":
                result = review_film(project, review["feedback"])
            else:
                result = refine_clip(project, directory, review)
                result["max_prompt_length"] = MAX_REVISED_PROMPT
        atomic_json(file, {**review, **result, "status": "ready"})
    except Exception as error:
        print(f"Studio refinement {directory.name}: {error}", file=sys.stderr, flush=True)
        message = str(error) if isinstance(error, ValueError) else "Studio could not complete the review. Try again."
        atomic_json(file, {**review, "status": "failed", "error": message})
    finally:
        with QUEUE_LOCK:
            ACTIVE_REFINEMENTS.discard(directory)


def stage_refinement(project, payload):
    with QUEUE_LOCK:
        _, index, feedback = revision_inputs(project, payload)
        directory = project / "refinements" / uuid.uuid4().hex
        review = {"id": directory.name, "shot": index, "feedback": feedback, "status": "queued"}
        atomic_json(directory / "review.json", review)
        ACTIVE_REFINEMENTS.add(directory)
        QUEUE.submit(run_refinement, project, directory)
    return review


def stage_film_review(project, payload):
    directory = project / "film-review"
    feedback = payload.get("feedback", "")
    if not isinstance(feedback, str) or len(feedback) > MAX_FEEDBACK:
        raise ValueError(f"Describe what to review in up to {MAX_FEEDBACK:,} characters.")
    with QUEUE_LOCK:
        if not (project / "film.mp4").is_file() or not (project / "production.json").is_file():
            raise ValueError("Wait for the movie to finish before reviewing all clips.")
        if directory in ACTIVE_REFINEMENTS:
            raise ValueError("A movie review is already running.")
        review = {"id": uuid.uuid4().hex, "feedback": feedback.strip(), "status": "queued"}
        atomic_json(directory / "review.json", review)
        ACTIVE_REFINEMENTS.add(directory)
        QUEUE.submit(run_refinement, project, directory)
    return review


def project_data(project, details=False):
    status = "done" if (project / "film.mp4").is_file() else (project / "status").read_text(encoding="utf-8")
    meta = read_json(project / "project.json") if (project / "project.json").is_file() else {}
    state = read_json(project / "progress.json") if (project / "progress.json").is_file() else {"stage": status}
    if status in ("queued", "running") and (project / "cancel.request").is_file():
        state = {**state, "stage": "cancelling"}
    result = {**meta, "id": project.name, "status": status, "progress": state}
    if details:
        result["can_revise"] = status == "done" and (project / "production.json").is_file()
        latest_reviews = {}
        for path in sorted(project.glob("refinements/*/review.json"), key=lambda file: file.parent.stat().st_mtime):
            review = read_json(path)
            latest_reviews[review["shot"]] = review
        result["refinements"] = latest_reviews
        film_review = project / "film-review" / "review.json"
        result["film_review"] = read_json(film_review) if film_review.is_file() else None
        transitions = project / "transitions.json"
        result["transitions"] = read_json(transitions) if transitions.is_file() else {}
        production = project / "production.json"
        result["handoffs"] = read_json(production).get("handoffs", {}) if production.is_file() else {}
        result["story"] = (project / "story.txt").read_text(encoding="utf-8")
        result["references"] = [path.name for path in sorted(project.glob("reference-*"))]
        result["image_notes"] = read_reference_notes(project)
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
        if read_json(project / "progress.json").get("stage") == "cancelled":
            set_status(project, "cancelled")
            return
        if not (project / "film.mp4").is_file():
            raise RuntimeError("Production ended without a video file.")
        set_status(project, "done")
    except Exception as error:
        state_file = project / "progress.json"
        state = read_json(state_file) if state_file.is_file() else {}
        if state.get("stage") != "failed":
            progress(project, "failed", error="Production was interrupted. Check the production log and resume the project.")
        set_status(project, "failed")
        print(f"Studio project {project.name}: {error}", file=sys.stderr, flush=True)
    finally:
        with QUEUE_LOCK:
            ACTIVE.discard(project.name)


def schedule(project, resume=False):
    with QUEUE_LOCK:
        if (not (project / "status").is_file() or project.name in ACTIVE
                or (project / "film.mp4").is_file()
                or (resume and (project / "status").read_text(encoding="utf-8") != "failed")):
            return False
        ACTIVE.add(project.name)
        set_status(project, "queued")
        progress(project, "queued")
        QUEUE.submit(render, project, resume)
    return True


def managed_projects():
    return [path.parent for path in ROOT.glob("*/status")
            if re.fullmatch(r"[a-f0-9]{32}", path.parent.name) and not path.parent.is_symlink()]


def request_cancellation(projects):
    requested = 0
    with QUEUE_LOCK:
        for project in projects:
            status = project / "status"
            if status.is_file() and status.read_text(encoding="utf-8") in ("queued", "running"):
                (project / "cancel.request").touch()
                requested += 1
    return requested


def clear_projects():
    with QUEUE_LOCK:
        projects = managed_projects()
        if ACTIVE or ACTIVE_REFINEMENTS or any((project / "status").read_text(encoding="utf-8") in ("queued", "running")
                         for project in projects):
            raise ValueError("Stop active productions before clearing projects.")
        for project in projects:
            shutil.rmtree(project)
    return len(projects)


def analyze_source(job, directory):
    try:
        job.update(result=analyze(Path(directory.name) / "source.mp4"))
    except ValueError as error:
        job.update(error=str(error))
    except subprocess.SubprocessError:
        job.update(error="Studio could not analyze this MP4. Try a shorter excerpt or re-export the video.")
    except Exception as error:
        print(f"Studio remake analysis: {error}", file=sys.stderr, flush=True)
        job.update(error="Remake analysis is unavailable. Check Qwen and FFmpeg on the pod.")
    finally:
        directory.cleanup()
        with REMAKE_LOCK:
            job["running"] = False


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
            self.json(403, error="Open Studio through localhost.")
            return False
        expected = self.server.studio_auth
        if not expected:
            self.reply(503, "text/plain; charset=utf-8",
                       "Set STUDIO_PASSWORD in the Runpod environment and restart the pod.".encode())
            return False
        supplied = self.headers.get("Authorization", "").encode()
        if hmac.compare_digest(supplied, expected):
            return True
        self.reply(401, "text/plain; charset=utf-8", "Sign in as studio.".encode(), {
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
                    return self.json(416, error="Invalid byte range.")
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
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True
                    return
                remaining -= len(chunk)

    def do_GET(self):
        if self.path == "/health":
            return self.json(200, status="ok")
        if not self.allowed():
            return
        if self.path == "/startup-status":
            return self.json(200, **startup_status())
        remake = re.fullmatch(r"/remakes/analyze/([a-f0-9]{32})", self.path)
        if remake:
            with REMAKE_LOCK:
                job = REMAKE_ANALYSIS.copy()
            if remake[1] != job.get("id"):
                return self.json(404, error="This analysis has expired. Analyze the video again.")
            if job["running"]:
                return self.json(202, running=True)
            if "error" in job:
                return self.json(422, error=job["error"])
            return self.json(200, **job["result"])
        if self.path == "/profiles":
            return self.json(200, profiles=render_profiles())
        if self.path == "/writer-prompt":
            return self.json(200, prompt=DEFAULT_WRITER_PROMPT, max_length=MAX_WRITER_PROMPT)
        if self.path == "/render-options":
            try:
                options = RenderClient().model_options()
                defaults = default_settings()
            except (OSError, ValueError, RenderError):
                return self.json(503, error="ComfyUI is not ready yet. Try reloading the models.")
            return self.json(200, **options, defaults=defaults, max_loras=MAX_LORAS)
        if self.path == "/":
            return self.reply(200, "text/html; charset=utf-8", (HERE / "studio.html").read_bytes())
        refinement = re.fullmatch(
            r"/projects/([a-f0-9]{32})/refinements/([a-f0-9]{32})(?:/((?:frame-[1-9]\d?|previous-last|next-first)\.jpg))?", self.path)
        if refinement:
            directory = ROOT / refinement[1] / "refinements" / refinement[2]
            review = directory / "review.json"
            if not review.is_file():
                return self.json(404, error="Clip refinement not found.")
            if refinement[3]:
                frame = directory / refinement[3]
                if not frame.is_file():
                    return self.json(404, error="Review frame not found.")
                return self.serve_file(frame)
            return self.json(200, **read_json(review))
        if self.path == "/projects":
            projects = [project_data(project) for project in managed_projects()]
            return self.json(200, projects=sorted(projects, key=lambda item: item.get("created", ""), reverse=True))
        match = re.fullmatch(r"/projects/([a-f0-9]{32})(?:/(film\.mp4|shots\.json|render\.log|reference-\d{2}\.(?:png|jpg|webp)|clips/shot-\d{2,}\.mp4|cuts/(\d+)))?", self.path)
        if not match:
            return self.json(404, error="Project not found.")
        project = ROOT / match[1]
        if not (project / "status").is_file():
            return self.json(404, error="Project not found.")
        if match[3]:
            index = int(match[3])
            shots = project / "shots.json"
            if (not (project / "film.mp4").is_file() or not shots.is_file()
                    or not 1 <= index < len(read_json(shots)["shots"])):
                return self.json(404, error="This transition is not available yet.")
            try:
                durations = [clip_duration(project / "clips" / f"shot-{number:02d}.mp4")
                             for number in range(1, index + 2)]
                if any(not math.isfinite(duration) or duration <= 0 for duration in durations):
                    raise ValueError("Invalid clip duration")
            except (OSError, ValueError, subprocess.SubprocessError):
                return self.json(503, error="Studio could not find this transition in the movie.")
            boundary = sum(durations[:-1])
            return self.json(200, start=boundary - min(2, durations[-2]),
                             end=boundary + min(2, durations[-1]))
        if match[2]:
            path = project / match[2]
            if not path.is_file():
                return self.json(404, error="The file is not available yet.")
            return self.serve_file(path)
        self.json(200, **project_data(project, details=True))

    def analyze_remake(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_VIDEO:
            return self.json(413, error="Choose an MP4 video up to 1 GB.")
        with REMAKE_LOCK:
            busy = REMAKE_ANALYSIS.get("running")
            if not busy:
                REMAKE_ANALYSIS.clear()
                REMAKE_ANALYSIS.update(id=uuid.uuid4().hex, running=True)
        if busy:
            return self.json(409, error="Studio is already analyzing a video. Try again when it finishes.")
        directory = None
        try:
            directory = TemporaryDirectory(prefix="studio-remake-")
            with (Path(directory.name) / "source.mp4").open("wb") as output:
                while length:
                    chunk = self.rfile.read(min(length, 1024 * 1024))
                    if not chunk:
                        raise ValueError("The video upload was interrupted.")
                    output.write(chunk)
                    length -= len(chunk)
            job_id = REMAKE_ANALYSIS["id"]
            threading.Thread(target=analyze_source, args=(REMAKE_ANALYSIS, directory), daemon=True).start()
        except (ValueError, OSError, RuntimeError):
            if directory is not None:
                directory.cleanup()
            with REMAKE_LOCK:
                REMAKE_ANALYSIS.clear()
            return self.json(400, error="Studio could not receive the video upload.")
        return self.json(202, id=job_id)

    def do_POST(self):
        if not self.allowed():
            return
        origin = getattr(self.server, "public_origin", None) or f"http://{self.headers.get('Host')}"
        content_type = "video/mp4" if self.path == "/remakes/analyze" else "application/json"
        if self.headers.get("Origin") != origin or self.headers.get_content_type() != content_type:
            return self.json(403, error="Open Studio from its own address.")
        if self.path == "/remakes/analyze":
            return self.analyze_remake()
        stop = re.fullmatch(r"/projects/([a-f0-9]{32})/cancel", self.path)
        if stop:
            if not request_cancellation([ROOT / stop[1]]):
                return self.json(409, error="This project is no longer active.")
            return self.json(202, requested=1)
        if self.path == "/projects/cancel":
            return self.json(202, requested=request_cancellation(managed_projects()))
        if self.path == "/projects/clear":
            try:
                return self.json(200, removed=clear_projects())
            except ValueError as error:
                return self.json(409, error=str(error))
            except OSError:
                return self.json(500, error="Studio could not clear all project files.")
        retry = re.fullmatch(r"/projects/([a-f0-9]{32})/resume", self.path)
        if retry:
            project = ROOT / retry[1]
            if not (project / "status").is_file():
                return self.json(404, error="Project not found.")
            if not schedule(project, resume=True):
                return self.json(409, error="This project cannot be resumed.")
            return self.json(202, id=project.name)
        revision = re.fullmatch(r"/projects/([a-f0-9]{32})/(revisions|refinements|film-review|film-revision)", self.path)
        if self.path not in {"/projects", "/remakes"} and not revision:
            return self.json(404, error="Unknown address.")
        if revision and not (ROOT / revision[1] / "status").is_file():
            return self.json(404, error="Project not found.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= (192 * 1024 if revision else MAX_REQUEST):
                return self.json(413, error="The upload is too large or empty.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Invalid project.")
            if revision and revision[2] == "refinements":
                review = stage_refinement(ROOT / revision[1], payload)
                return self.json(202, **review)
            if revision and revision[2] == "film-review":
                return self.json(202, **stage_film_review(ROOT / revision[1], payload))
            if self.path == "/remakes":
                if payload.get("approved") is not True or payload.get("aspect") not in tuple(ASPECT_SIZES):
                    raise ValueError("Review and approve the remake before rendering.")
                plan = compile_plan(payload)
                project = stage({"story": plan["story"], "images": payload["images"],
                                 "aspect": payload["aspect"],
                                 "profile": payload.get("profile", DEFAULT_PROFILE["id"]),
                                 "render_settings": payload.get("render_settings")}, plan=plan)
            elif revision and revision[2] == "film-revision":
                project = stage_film_revision(ROOT / revision[1], payload)
            else:
                project = stage_revision(ROOT / revision[1], payload) if revision else stage(payload)
        except (ValueError, TypeError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            return self.json(400, error=str(error) if isinstance(error, ValueError) else "The reference image could not be read. Use JPEG, PNG, or WebP.")
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
        raise SystemExit("Invalid RUNPOD_POD_ID.")
    server = ThreadingHTTPServer(("0.0.0.0" if pod_id else "127.0.0.1", args.port), StudioHandler)
    server.public_origin = f"https://{pod_id}-{args.port}.proxy.runpod.net" if pod_id else None
    password = os.environ.get("STUDIO_PASSWORD", "")
    server.studio_auth = None
    if len(password) >= 16 and "{{" not in password:
        server.studio_auth = b"Basic " + base64.b64encode(("studio:" + password).encode())
    for status_file in ROOT.glob("*/status"):
        if status_file.read_text(encoding="utf-8") in ("queued", "running"):
            schedule(status_file.parent)
    for project in managed_projects():
        review_files = list(project.glob("refinements/*/review.json"))
        film_review = project / "film-review" / "review.json"
        if film_review.is_file():
            review_files.append(film_review)
        for review_file in review_files:
            review = read_json(review_file)
            if review["status"] in ("queued", "reviewing"):
                directory = review_file.parent
                with QUEUE_LOCK:
                    ACTIVE_REFINEMENTS.add(directory)
                    QUEUE.submit(run_refinement, project, directory)
    print(f"H3 Video Studio {server.public_origin or f'http://localhost:{args.port}'}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        QUEUE.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()

"""Collect clip evidence and draft a revision without submitting a render."""
from pathlib import Path
import math
import subprocess

from studio_storage import read_json
from studio_writer import DEFAULT_WRITER_PROMPT, suggest_revision

MAX_FEEDBACK = 4000
MAX_REVISED_PROMPT = 12000


def revision_inputs(project, payload):
    if not (project / "film.mp4").is_file() or not (project / "production.json").is_file():
        raise ValueError("Wait for the film to finish before revising a shot.")
    index, feedback = payload.get("shot"), payload.get("feedback")
    plan = read_json(project / "production.json")["plan"]
    if type(index) is not int or not 0 <= index < len(plan["shots"]):
        raise ValueError("Choose an existing shot.")
    if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > MAX_FEEDBACK:
        raise ValueError(f"Describe what should change in up to {MAX_FEEDBACK:,} characters.")
    return plan, index, feedback.strip()


def review_timestamps(duration):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("The clip duration could not be read.")
    count = max(3, min(15, math.ceil(duration * 1.5) + 1))
    margin = min(0.08, duration * 0.1)
    spacing = (duration - 2 * margin) / (count - 1)
    return [margin + index * spacing for index in range(count)]


def clip_evidence(clip, directory):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
        capture_output=True, text=True, check=True, timeout=15,
    )
    duration = float(result.stdout.strip())
    frames = []
    for number, timestamp in enumerate(review_timestamps(duration), 1):
        frame = directory / f"frame-{number}.jpg"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{timestamp:.6f}",
             "-i", str(clip), "-frames:v", "1",
             "-vf", "scale=512:512:force_original_aspect_ratio=decrease",
             "-q:v", "4", str(frame)],
            check=True, capture_output=True, timeout=20,
        )
        if frame.is_file() and frame.stat().st_size:
            frames.append((timestamp, frame))
    if not frames:
        raise ValueError("No review frames could be extracted from this clip.")
    return frames


def refine_clip(project, directory, request):
    plan, index, feedback = revision_inputs(project, request)
    clip = project / "clips" / f"shot-{index + 1:02d}.mp4"
    if not clip.is_file():
        raise ValueError("The selected clip is unavailable.")
    frames = clip_evidence(clip, directory)
    pictures = [(f"image{number}", path) for number, path in
                enumerate(sorted(project.glob("reference-*")), 17)]
    instructions = project / "writer-prompt.txt"
    writer_prompt = instructions.read_text(encoding="utf-8") if instructions.is_file() else DEFAULT_WRITER_PROMPT
    proposal = suggest_revision(plan, index, feedback, pictures, writer_prompt, clip_frames=frames)
    if len(proposal["prompt"]) > MAX_REVISED_PROMPT:
        raise ValueError("The proposed prompt is too long. Try refining the clip with more focused feedback.")
    return {
        **proposal,
        "frames": [{"file": frame.name, "seconds": round(timestamp, 2)} for timestamp, frame in frames],
    }

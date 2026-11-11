"""Collect clip evidence and draft a revision without submitting a render."""
from pathlib import Path
import json
import math
import subprocess
from tempfile import TemporaryDirectory

from studio_storage import atomic_json, check_cancelled, progress, read_json
from studio_writer import DEFAULT_WRITER_PROMPT, ask_qwen, read_reference_notes, suggest_revision

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


def clip_duration(clip):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
        capture_output=True, text=True, check=True, timeout=15,
    )
    return float(result.stdout.strip())


def clip_evidence(clip, directory, timestamps=None):
    if timestamps is None:
        timestamps = review_timestamps(clip_duration(clip))
    frames = []
    for number, timestamp in enumerate(timestamps, 1):
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


def review_transition(outgoing, incoming, directory, planned_before, planned_after):
    frames = []
    for clip, label, at_end in ((outgoing, "Outgoing shot", True), (incoming, "Incoming shot", False)):
        duration = clip_duration(clip)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("The clip duration could not be read.")
        margin = min(0.09, duration / 8)
        offset = min(0.45, duration / 3)
        timestamps = [duration - offset, duration - margin] if at_end else [margin, offset]
        frame_dir = directory / label
        frame_dir.mkdir()
        evidence = clip_evidence(clip, frame_dir, timestamps)
        if len(evidence) != len(timestamps):
            raise ValueError("Transition frames are incomplete.")
        frames.extend((timestamp, path, label) for timestamp, path in evidence)
    result = ask_qwen(
        "Review the visual transition using the labeled frames on both sides. Compare visible subject identity, "
        "pose, screen direction, movement, wardrobe, lighting and object placement when relevant. "
        "Use the planned actions and deliberate_cut flag to judge whether a change is intentional. "
        "A deliberate cut may change framing, time or location. Use only visible evidence and state uncertainty "
        "when the frames are insufficient. Return JSON with verdict (pass, needs_attention or uncertain), "
        "observations (one or two short English sentences), and suggestion (a short, specific English note "
        "for refining the incoming shot if needed, otherwise an empty string).",
        json.dumps({"outgoing_action": planned_before["action"], "incoming_action": planned_after["action"],
                    "deliberate_cut": planned_after.get("cut", False)}, ensure_ascii=False),
        [], clip_frames=frames, timeout=60,
    )
    if (not isinstance(result, dict) or result.get("verdict") not in ("pass", "needs_attention", "uncertain")
            or not isinstance(result.get("observations"), str) or not result["observations"].strip()
            or not isinstance(result.get("suggestion"), str)):
        raise ValueError("Qwen returned an invalid transition review.")
    return {key: result[key].strip()[:1000] for key in ("verdict", "observations", "suggestion")}


def review_transitions(project, shots):
    report = project / "transitions.json"
    reviews = read_json(report) if report.is_file() else {}
    for index in range(1, len(shots)):
        check_cancelled(project)
        progress(project, "reviewing", completed=index - 1, total=len(shots) - 1)
        if reviews.get(str(index), {}).get("verdict") in ("pass", "needs_attention", "uncertain"):
            continue
        try:
            with TemporaryDirectory() as temporary:
                result = review_transition(
                    project / "clips" / f"shot-{index:02d}.mp4",
                    project / "clips" / f"shot-{index + 1:02d}.mp4",
                    Path(temporary), shots[index - 1], shots[index],
                )
        except (OSError, RuntimeError, ValueError, LookupError, subprocess.SubprocessError) as error:
            print(f"Transition into shot {index + 1} could not be reviewed: {error}", flush=True)
            result = {"verdict": "unavailable", "observations": "Studio could not review this transition."}
        check_cancelled(project)
        reviews[str(index)] = result
        atomic_json(report, reviews)


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
    proposal = suggest_revision(
        plan, index, feedback, pictures, writer_prompt, clip_frames=frames,
        reference_notes=read_reference_notes(project),
    )
    if len(proposal["prompt"]) > MAX_REVISED_PROMPT:
        raise ValueError("The proposed prompt is too long. Try refining the clip with more focused feedback.")
    return {
        **proposal,
        "frames": [{"file": frame.name, "seconds": round(timestamp, 2)} for timestamp, frame in frames],
    }

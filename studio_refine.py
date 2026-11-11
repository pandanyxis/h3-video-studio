"""Collect clip evidence and draft a revision without submitting a render."""
from pathlib import Path
import json
import math
import subprocess
from tempfile import TemporaryDirectory

from studio_storage import atomic_json, check_cancelled, progress, read_json
from studio_writer import (DEFAULT_WRITER_PROMPT, ask_qwen, bind_shot_references,
                           read_reference_notes, referenced_slots, suggest_revision)

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


def extract_review_frame(clip, frame, *, timestamp=None, last=False):
    seek = [] if timestamp is None else ["-ss", f"{timestamp:.6f}"]
    filters = "scale=512:512:force_original_aspect_ratio=decrease"
    if last:
        filters += ",reverse"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *seek, "-i", str(clip),
         "-map", "0:v:0", "-frames:v", "1", "-vf", filters, "-q:v", "4", str(frame)],
        check=True, capture_output=True, timeout=20,
    )
    return frame.is_file() and frame.stat().st_size > 0


def clip_evidence(clip, directory, timestamps=None):
    if timestamps is None:
        timestamps = review_timestamps(clip_duration(clip))
    frames = []
    for number, timestamp in enumerate(timestamps, 1):
        frame = directory / f"frame-{number}.jpg"
        if extract_review_frame(clip, frame, timestamp=timestamp):
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
        offset = min(0.45, duration / 3)
        frame_dir = directory / label
        frame_dir.mkdir()
        edge = frame_dir / "boundary.jpg"
        if not extract_review_frame(clip, edge, last=at_end):
            raise ValueError("Transition frames are incomplete.")
        nearby_time = duration - offset if at_end else offset
        nearby = frame_dir / "nearby.jpg"
        if not extract_review_frame(clip, nearby, timestamp=nearby_time):
            raise ValueError("Transition frames are incomplete.")
        context = (nearby_time, nearby, label)
        frames.extend(
            (context, (None, edge, "Outgoing shot: exact last video frame"))
            if at_end else ((None, edge, "Incoming shot: exact first video frame"), context)
        )
    result = ask_qwen(
        "Review whether the visual transition feels natural using the labeled frames on both sides. "
        "Compare the exact last frame of the outgoing shot with the exact first frame of the incoming shot "
        "first. Use the nearby frames to assess how movement leads into and out of this boundary. "
        "Prioritize spatial continuity. Match each visible person across the boundary and compare where "
        "they end and begin relative to fixed landmarks and other people. Flag unexplained relocations, "
        "people swapping places, or sudden changes in distance during continuous action. A new camera "
        "angle can change screen position without moving the person in the scene. If the view lacks "
        "shared landmarks or reliable spatial cues, state the uncertainty. For a position mismatch, "
        "identify the person, the visible anchor, and where the incoming shot should begin. "
        "For continuous action, also check whether posture, limb positions, "
        "direction and pace carry through plausibly. Look for sudden resets, jumps, or limbs merging between "
        "people. Flag a visible reset of a story action, such as a door that was just opened being shut again "
        "at the start of the next shot without explanation. Compare visible garments, layers and accessories "
        "for each person. Flag clothing that suddenly "
        "appears, disappears or changes during continuous action, allowing for framing and occlusion. "
        "Also check identity, lighting and object placement when relevant. Use the planned actions and "
        "deliberate_cut flag to judge the intended transition. A new camera angle alone does not explain a "
        "wardrobe change. An explicit clothing change or time jump may explain it. Use only visible evidence "
        "and state uncertainty when the frames are insufficient. "
        "Return JSON with verdict (pass, needs_attention or uncertain), "
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
    previous, following = [], []
    for neighbor, target, name, label, last in (
        (index - 1, previous, "previous-last.jpg", "Previous shot: exact last video frame", True),
        (index + 1, following, "next-first.jpg", "Next shot: exact first video frame", False),
    ):
        if not 0 <= neighbor < len(plan["shots"]):
            continue
        source = clip.with_name(f"shot-{neighbor + 1:02d}.mp4")
        frame = directory / name
        if source.is_file() and extract_review_frame(source, frame, last=last):
            target.append((None, frame, label))
    pictures = [(f"image{number}", path) for number, path in
                enumerate(sorted(project.glob("reference-*")), 17)]
    instructions = project / "writer-prompt.txt"
    writer_prompt = instructions.read_text(encoding="utf-8") if instructions.is_file() else DEFAULT_WRITER_PROMPT
    proposal = suggest_revision(
        plan, index, feedback, pictures, writer_prompt, clip_frames=[*previous, *frames, *following],
        reference_notes=read_reference_notes(project),
    )
    if len(proposal["prompt"]) > MAX_REVISED_PROMPT:
        raise ValueError("The proposed prompt is too long. Try refining the clip with more focused feedback.")
    return {
        **proposal,
        "frames": [{"file": frame.name, "seconds": round(timestamp, 2)} for timestamp, frame in frames],
        "neighbor_frames": [{"file": frame.name, "label": label} for _, frame, label in previous + following],
    }


def review_film(project, feedback):
    plan = read_json(project / "production.json")["plan"]
    report = project / "transitions.json"
    transitions = read_json(report) if report.is_file() else {}
    with TemporaryDirectory() as temporary:
        frames = []
        for index, _ in enumerate(plan["shots"], 1):
            clip = project / "clips" / f"shot-{index:02d}.mp4"
            duration = clip_duration(clip)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError(f"Could not read the duration of shot {index}.")
            for sample, timestamp, last in (("first", None, False), ("middle", duration / 2, False),
                                             ("last", None, True)):
                frame = Path(temporary) / f"shot-{index:02d}-{sample}.jpg"
                if not extract_review_frame(clip, frame, timestamp=timestamp, last=last):
                    raise ValueError(f"Could not read the {sample} frame of shot {index}.")
                frames.append((None, frame, f"Shot {index}: {sample} video frame"))
        result = ask_qwen(
            "Review the rendered clips together as one film. Compare the intended story, the ordered shot plan, "
            "the user feedback and the labeled rendered frames. Check whether actions and reactions build on "
            "one another, story beats repeat or go missing, and adjacent shots join naturally. Treat earlier "
            "transition reports as supporting evidence. State uncertainty for motion, sound, or events that "
            "the sampled frames cannot show. Return JSON with summary (a brief English overview) and reviews "
            "(one object per shot). Each object needs shot (one-based number) and observations (a short English "
            "assessment). For an actionable issue also provide feedback (a concise English explanation of "
            "the change) and prompt (the full replacement English H3 prompt). Otherwise omit feedback and prompt. "
            "Preserve required references, use only image labels found in the plan, and preserve the duration "
            "and intended beat. Coordinate replacements with adjacent shots so they join naturally.",
            json.dumps({"story": plan["story"], "shots": plan["shots"], "feedback": feedback,
                        "transition_reviews": transitions}, ensure_ascii=False),
            [], clip_frames=frames, timeout=240,
        )
    if (not isinstance(result, dict) or not isinstance(result.get("summary"), str)
            or not result["summary"].strip() or not isinstance(result.get("reviews"), list)):
        raise ValueError("Qwen returned an invalid film review.")
    reviews = {}
    slots = {f"image{number}" for number, _ in enumerate(sorted(project.glob("reference-*")), 17)}
    for item in result["reviews"]:
        if not isinstance(item, dict):
            raise ValueError("Qwen returned an invalid film review note.")
        shot, observation = item.get("shot"), item.get("observations")
        if (type(shot) is not int or not 1 <= shot <= len(plan["shots"]) or shot in reviews
                or not isinstance(observation, str) or not observation.strip()):
            raise ValueError("Qwen returned an invalid film review note.")
        entry = {"observations": observation.strip()[:MAX_FEEDBACK]}
        note, prompt = item.get("feedback"), item.get("prompt")
        if note or prompt:
            if not isinstance(note, str) or not note.strip() or not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("Qwen returned an incomplete shot revision.")
            original = plan["shots"][shot - 1]
            prompt = bind_shot_references(prompt.strip(), original, slots,
                                          existing=referenced_slots(original["prompt"]))
            if len(prompt) > MAX_REVISED_PROMPT:
                raise ValueError("Qwen returned a replacement prompt that is too long.")
            entry.update(feedback=note.strip()[:MAX_FEEDBACK], prompt=prompt)
        reviews[shot] = entry
    if len(reviews) != len(plan["shots"]):
        raise ValueError("Qwen did not review every shot. Try the film review again.")
    return {"summary": result["summary"].strip()[:MAX_FEEDBACK], "reviews": reviews}

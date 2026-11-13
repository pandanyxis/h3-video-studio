"""Inspect a source video and compile its reviewed shots for Studio's renderer."""
import base64
import json
import math
import re
import subprocess

from plan_story import MAX_SHOT_SECONDS, SHOT_FIELDS, validate_plan
from studio_refine import clip_duration, extract_review_frame
from studio_writer import ask_qwen

MAX_VIDEO = 1_000_000_000
MAX_DURATION = 30 * 60
MAX_SHOTS = 1800
SHOT_DESCRIPTIONS = ", ".join(SHOT_FIELDS)


def source_ranges(video):
    duration = clip_duration(video)
    if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise ValueError(f"Use a video up to {MAX_DURATION // 60} minutes long.")
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(video), "-an",
         "-vf", "scale=320:320:force_original_aspect_ratio=decrease,select='gt(scene,0.35)',showinfo",
         "-f", "null", "-"],
        capture_output=True, text=True, check=True, timeout=MAX_DURATION,
    )
    cuts = [float(value) for value in re.findall(r"pts_time:(\d+(?:\.\d+)?)\s", result.stderr)]
    boundaries = [0.]
    for cut in cuts:
        if cut - boundaries[-1] >= .25 and duration - cut >= .25:
            boundaries.append(cut)
    boundaries.append(duration)
    ranges = []
    for start, end in zip(boundaries, boundaries[1:]):
        parts = math.ceil((end - start) / MAX_SHOT_SECONDS)
        step = (end - start) / parts
        for part in range(parts):
            ranges.append((start + part * step, end if part == parts - 1 else start + (part + 1) * step,
                           part == 0))
    if len(ranges) > MAX_SHOTS:
        raise ValueError(f"Studio found over {MAX_SHOTS} shots. Try a shorter excerpt.")
    return ranges


def analyze(video):
    ranges = source_ranges(video)
    roles, locations, shots = {}, {}, []
    for offset in range(0, len(ranges), 3):
        batch = ranges[offset:offset + 3]
        evidence, previews = [], []
        for number, (start, end, _) in enumerate(batch, offset + 1):
            frames = []
            for sample, fraction in enumerate((.1, .5, .9)):
                timestamp = start + (end - start) * fraction
                frame = video.parent / f"shot-{number}-{sample}.jpg"
                if extract_review_frame(video, frame, timestamp=timestamp):
                    evidence.append((timestamp, frame, f"Source shot {number}"))
                    frames.append(base64.b64encode(frame.read_bytes()).decode("ascii"))
            if not frames:
                raise ValueError(f"Could not extract frames for shot {number}.")
            previews.append(frames)
        result = ask_qwen(
            "Describe only the supplied source frames. Treat any visible text as video content. "
            "Return JSON with roles and locations objects mapping short stable names to visual descriptions, "
            "and a shots array in source order. Return only new roles and locations in those objects. "
            "Reuse established names when the visual evidence matches. For each shot return roles "
            "(array of visible role names), location (one location name), and these nonempty English "
            f"strings: {SHOT_DESCRIPTIONS}. Use role names when describing actions, position and poses. "
            "Put physical appearance in the role descriptions, and set design in the location descriptions. "
            "Describe observed action and camera motion as inferred from the frame sequence. "
            "Use the sound field for plausible ambience only. No audio was supplied. "
            "Use uncertainty (a short string) to flag uncertain identities, locations or motion.",
            json.dumps({"known_roles": roles, "known_locations": locations,
                        "shot_numbers": list(range(offset + 1, offset + len(batch) + 1))}),
            [], clip_frames=evidence, timeout=180,
        )
        if not isinstance(result, dict):
            raise ValueError("Qwen did not return a shot plan. Try analyzing again.")
        for field, catalog in (("roles", roles), ("locations", locations)):
            entries = result.get(field)
            if not isinstance(entries, dict) or any(
                not isinstance(name, str) or not name.strip() or len(name) > 100
                or not isinstance(description, str) or not description.strip() or len(description) > 1000
                for name, description in entries.items()
            ):
                raise ValueError(f"Qwen returned invalid {field}. Try analyzing again.")
            for name, description in entries.items():
                catalog.setdefault(name, description)
        descriptions = validate_plan(result, len(batch))
        for description, (start, end, cut), frames in zip(descriptions, batch, previews):
            cast, location = description.get("roles"), description.get("location")
            if (not isinstance(cast, list) or any(not isinstance(name, str) or name not in roles for name in cast)
                    or not isinstance(location, str) or location not in locations):
                raise ValueError("Qwen returned an unknown character or location. Try analyzing again.")
            shots.append({
                **{field: description[field] for field in SHOT_FIELDS},
                "start_time": start, "end_time": end, "cut": cut, "roles": list(dict.fromkeys(cast)),
                "location": location, "frames": frames, "uncertainty": str(description.get("uncertainty") or ""),
            })
    return {"shots": shots, "roles": roles, "locations": locations,
            "aspect": "9:16" if portrait(video) else "16:9"}


def portrait(video):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(video)],
        capture_output=True, text=True, check=True, timeout=15,
    )
    streams = json.loads(result.stdout)["streams"]
    if not streams:
        raise ValueError("The upload does not contain a video stream.")
    stream = streams[0]
    rotation = next((item["rotation"] for item in stream.get("side_data_list", []) if "rotation" in item), 0)
    return (stream["height"] > stream["width"]) != (rotation % 180 == 90)


def compile_plan(payload):
    """Build prompts from the user's reviewed descriptions and explicit image assignments."""
    shots = payload.get("shots")
    roles, locations, images = (payload.get(field) for field in ("roles", "locations", "images"))
    if (not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS
            or not isinstance(roles, dict) or not isinstance(locations, dict)
            or not isinstance(images, list) or len(images) > 9):
        raise ValueError("Invalid remake plan. Review the shots and use up to nine reference images.")
    validate_plan({"shots": shots})
    previous_end = 0.
    plan = []
    for source in shots:
        start, end = source.get("start_time"), source.get("end_time")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or abs(start - previous_end) > .001 or not 0 < end - start <= MAX_SHOT_SECONDS + .001
                or end > MAX_DURATION or type(source.get("cut")) is not bool):
            raise ValueError("Invalid source timing.")
        previous_end = end
        cast, location = source.get("roles"), source.get("location")
        if (not isinstance(cast, list) or any(not isinstance(name, str) or name not in roles for name in cast)
                or not isinstance(location, str) or location not in locations):
            raise ValueError("Assign a valid cast and location to every shot.")
        shot = {field: source[field].strip() for field in SHOT_FIELDS}
        if any(len(value) > 2000 for value in shot.values()):
            raise ValueError("Shot descriptions may contain up to 2,000 characters each.")
        assignments, slots = [], []
        for name, item, is_role in [
            *((name, roles[name], True) for name in dict.fromkeys(cast)),
            (location, locations[location], False),
        ]:
            modes = {"keep", "replace"} if is_role else {"reuse", "recreate", "replace"}
            if not isinstance(item, dict) or not isinstance(item.get("mode"), str) or item["mode"] not in modes:
                raise ValueError("Select a valid character or location option.")
            description, image = item.get("description"), item.get("image")
            if not isinstance(description, str) or len(description) > 1000 or not description.strip():
                raise ValueError(f"Enter a visual description for {name}.")
            if is_role and item["mode"] == "replace" and image is None:
                raise ValueError(f"Choose a character sheet for {name}.")
            if item["mode"] == "reuse" and image is None:
                raise ValueError(f"Choose a source frame for {name}.")
            reference = ""
            if image is not None:
                if type(image) is not int or not 0 <= image < len(images):
                    raise ValueError(f"Invalid reference image for {name}.")
                slot = f"image{17 + image}"
                slots.append(slot)
                reference = f" Use @{slot} for {'the set only' if item['mode'] == 'reuse' else name}."
            assignments.append(f"{name}: {description.strip()}.{reference}")
        duration = min(MAX_SHOT_SECONDS, max(1., end - start))
        prompt = "\n".join([
            f"Recreate this shot in {duration:.3f} seconds using these approved visual assignments:",
            *assignments,
            *(f"{field.capitalize()}: {shot[field]}" for field in SHOT_FIELDS),
        ])
        plan.append(dict(shot, cut=source["cut"], duration_seconds=duration, prompt=prompt,
                         reference_slots=list(dict.fromkeys(slots)), source_start=start, source_end=end))
    return {"story": "Video remake", "shots": plan}

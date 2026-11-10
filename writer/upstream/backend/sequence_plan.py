"""Ephemeral semantic schedule for one Sequence operation, independent of media routing."""
from __future__ import annotations

import json
import re

from .models.contract import ModelError


PLAN_INSTRUCTIONS = """Allocate the user's requested development over the supplied video intervals. Return only the JSON below. This temporary temporal plan allocates content; it does not establish a second version of the scene.
The brief and explicit instructions govern intent. Existing prompts marked KEEP, including manual edits, are fixed scene evidence: fit missing or revised intervals between their actual endings and openings without replaying completed events. For refinement, change only the requested interval. Never plan a replacement for a KEEP interval.
Plan the requested kind of progression: movement, interaction, speech, changing light, atmosphere, or a sustained state. An interval need not contain a new physical action. Preserve ordered events and their explicit timing. Keep short actions naturally paced; do not stretch one gesture over several intervals or move its execution earlier than its assigned time. If the brief names a concluding action or endpoint, reach it in the final interval unless the user places it earlier. Earlier intervals may prepare for a short concluding action, but perform that action within its assigned interval rather than splitting its execution across boundaries. If no conclusion is requested, end in a natural continuing state without inventing one. A frame boundary is not a stop, cut, or freeze.
Use only brief-grounded developments and modest natural transitions, without adding story, people, consequential props or locations. Honor requested camera behavior; otherwise do not invent camera moves or cuts. First/last frames constrain only the supplied boundary; their pixels are not available here, so defer visual facts to the writer rather than guessing them. The writer will read the actual accepted predecessor and effective media.
Use exactly {"steps":[{"progression":"what develops or continues during the interval", "ending":"what is true at its end, including ongoing activity"}]}. Return one step for each interval marked generate=true, in chronological order. Keep both strings concise. Do not duplicate appearance, scene inventories, the brief, or KEEP prompts. No indexes, extra keys, media labels, commentary, or Markdown; explicit event timing may be included in progression."""


def assemble_plan(state, body, targets, rows):
    intervals = []
    for i, row in enumerate(rows):
        item = {"interval": i + 1, "global_seconds": [row["start"], row["end"]],
                "direction": row["instruction"], "generate": i in targets}
        if i not in targets and row["prompt"].strip():
            item["KEEP existing prompt"] = row["prompt"]
        if body["action"] == "refine" and i in targets:
            item.update(current_prompt=row["prompt"], refinement=body["instruction"])
        intervals.append(item)
    text = json.dumps({"brief": state["brief"], "sequence_instructions": state["instructions"],
                       "first_frame": "opening only, pixels not supplied to this planning call" if state.get("first") else None,
                       "last_frame": "final only, pixels not supplied to this planning call" if state.get("last") else None,
                       "intervals": intervals, "required_step_count": len(targets),
                       "planning_task": "Use the supplied Sequence Instructions for creative constraints, not their final H3 output format. "
                                        "This call only allocates time. Return exactly {\"steps\":[{\"progression\":\"development or sustained activity\",\"ending\":\"state at the boundary\"}]} "
                                        f"with exactly {len(targets)} steps, one per generate=true interval. "
                                        "Do not combine or omit intervals even for a sparse brief; allocate continued development or a sustained state. Do not write H3 prompts."}, ensure_ascii=False)
    return {"schema_version": 1, "completion_policy": "single_call", "sequence_stage": "plan",
            "guide": {"id": "sequence_plan", "title": "Sequence temporal plan"},
            "input": {"mode": "T2VA", "duration_seconds": rows[-1]["end"],
                      "aspect_ratio": state["aspectRatio"], "creative_brief": state["brief"],
                      "media_manifest": {"session_id": body["session_id"], "mode": "T2VA", "assets": [], "valid": True}},
            "media_inputs": [], "supporting_guides": [],
            "system_prompt": {"custom": False, "content": PLAN_INSTRUCTIONS},
            "messages": [{"role": "system", "content": PLAN_INSTRUCTIONS}, {"role": "user", "content": text}]}


def parse_plan(text, count, intervals=None):
    """Validate once, distinguishing missing semantics from unsupported packaging."""
    def invalid(reason, message, **details):
        raise ModelError("INVALID_SEQUENCE_PLAN", message + " No new chunks were written. Run Generate Sequence again.",
                         {"stage": "planning", "reason": reason, "expected_steps": count, **details})

    if not isinstance(text, str) or not text.strip():
        invalid("empty_output", "The model returned no planning text.")
    value = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", value)
    if fence:
        value = fence[1]
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                invalid("duplicate_field", "The model repeated a planning field, so the plan is ambiguous.", field=key)
            result[key] = value
        return result
    try:
        plan = json.loads(value, object_pairs_hook=unique_fields)
    except json.JSONDecodeError as error:
        invalid("invalid_json", "The model returned an unreadable planning format.",
                diagnostic="planner returned invalid JSON", line=error.lineno, column=error.colno)
    if not isinstance(plan, dict) or set(plan) != {"steps"}:
        invalid("invalid_structure", "The model returned an unsupported planning structure.",
                received_fields=list(plan) if isinstance(plan, dict) else None)
    steps = plan["steps"]
    if not isinstance(steps, list):
        invalid("invalid_steps", "The model did not return a list of planning steps.")
    if len(steps) != count:
        invalid("step_count", f"The model returned {len(steps)} of {count} required planning steps.", received_steps=len(steps))
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            invalid("invalid_step", f"The model returned an unreadable planning step {index}.", step=index)
        for field in ("progression", "ending"):
            if field not in step or isinstance(step[field], str) and not step[field].strip():
                invalid("missing_field", f"The model omitted the {field} for planning step {index}.", step=index, field=field)
            if not isinstance(step[field], str) or len(step[field]) > 4000:
                invalid("invalid_field", f"The model returned an unsupported {field} for planning step {index}.", step=index, field=field)
        # Observed models sometimes echo a redundant interval number. Only an
        # exact match to the requested chronology is harmless packaging.
        if "interval" in step:
            expected = intervals[index - 1] if intervals is not None else index
            if type(step["interval"]) is not int or step["interval"] != expected:
                invalid("interval_mismatch", f"The model assigned planning step {index} to the wrong interval.",
                        step=index, expected_interval=expected, received_interval=step["interval"])
            step = {key: value for key, value in step.items() if key != "interval"}
            steps[index - 1] = step
        if set(step) != {"progression", "ending"}:
            invalid("extra_fields", f"The model added unsupported fields to planning step {index}.", step=index, fields=sorted(set(step) - {"progression", "ending"}))
    return steps


def interval_context(plan, index, compact=False):
    """Only allocation is inferred. Actual prompts, not planned past steps, own history."""
    step = plan[index]
    pending = "\n".join(f"- {p['progression']} → {p['ending']}" for i, p in sorted(plan.items()) if i > index) or "None"
    return ("\n\nTEMPORAL PLAN — internal allocation, not output sections\n"
            f"This interval: {step['progression']}\n"
            f"Intended ending: {step['ending']}\n"
            f"Reserved for later generated intervals:\n{pending}\n"
            "Start from the actual preceding clip's ending, or establish the opening from the brief and supplied first frame. "
            "Accepted prompts and effective media are evidence; this plan is only an allocation. If a planned event has already happened, "
            "continue its achieved state without replay. Fit any following KEEP prompt's opening. "
            + ("Write the development and ending in natural-language prose, with enough scene context to stand alone.\n" if compact else
               "Write the development and ending inside the official H3 fields, with enough scene context to stand alone.\n"))

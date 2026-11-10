"""Compact changes the final writing contract, not Sequence orchestration."""
import re

from .sequence_output import LABEL, STAMP, without_literals, invalid


COMPACT_CONTRACT = """Write one complete, self-contained descriptive video prompt in natural language.
Use clear paragraphs describing the scene, subjects, action and camera intent. Be concise without removing necessary detail.
This is Compact output: do not use the official Base/Reference section schema, task-type prefix, subject_definitions, retention_analysis or frame-alignment boilerplate. Do not return JSON, commentary or Markdown fences. This output contract overrides formatting directions in Sequence Instructions and neighboring prompts; retain their creative constraints.
Retain useful request-local <Picture N>, <Video N> or <Audio N> literals when they identify actual supplied media, explaining each used role naturally in prose. First constrains only the supplied opening and Last only the supplied ending. An appearance reference does not supply a new scene. Do not invent media labels. Audio is declared context, not analyzed sound.
Local timing still runs from zero to the target duration. Use a timestamp only where timing matters; never output global chunk labels. A chunk boundary is not a cut. Preserve requested dialogue, progression, camera intent and style.
After the standalone description, end with exactly these two fields, each on its own line:
overall_soundscape:
A short natural description of scene-grounded audible sounds. Modest obvious sounds such as footsteps, fabric rustle or room ambience are appropriate; do not add events or elaborate sound design. Preserve requested speech or singing as sound from the scene.
non_diegetic_music:
N/A unless the raw brief or instructions request music; then briefly describe only the requested music. An explicit no-music request means N/A. Singing or scene sound alone does not request background music. These two sound fields are required, including for quiet scenes.
Start from the current preceding prompt, develop this interval's allocation and reach its intended ending. Establish enough scene context for this prompt to work independently. Never replay a completed event just because the temporal plan mentions it."""


def validate_compact(prompt, duration, assets):
    text = without_literals(prompt)
    if not prompt.strip():
        invalid("the model returned no descriptive prompt")
    if "```" in text or re.match(r"\s*[\[{]", text) and not re.match(r"\s*\[Shot 1\]", text):
        invalid("the model returned a wrapper instead of a descriptive prompt")
    compact_parts(text)
    if re.search(r"(?mi)^\s*(?:subject_definitions|retention_analysis|integrated_multimodal_description|detailed_description|summary)\s*:", text):
        invalid("the model used official section markup instead of Compact prose", True)
    available = {a["reference"] for a in assets or []}
    for match in re.finditer(r"<(?:Subject|Picture|Video|Audio)\b", text, re.I):
        label = LABEL.match(text, match.start())
        if not label:
            invalid("the model returned a malformed reference label")
        if label[1] != "Subject" and label[0] not in available:
            invalid(f"the model used {label[0]}, which is absent from this chunk's media")
    for match in STAMP.finditer(text):
        if int(match[2]) >= 60 or int(match[1]) * 60 + int(match[2]) + int(match[3]) / 1000 > duration:
            invalid("the model returned a timestamp outside this chunk's duration")
    shots = re.findall(r"\[Shot (\d+)\]", text)
    if shots and shots[0] != "1":
        invalid("the model did not start local shot numbering at Shot 1")
    if prompt.count("<d>") != prompt.count("</d>"):
        invalid("the model returned incomplete dialogue markup")
    return prompt


def compact_parts(text):
    """Require prose and two nonempty sound fields, without inferring sound intent."""
    names = re.findall(r"(?mi)^\s*(overall_soundscape|non_diegetic_music)\s*:", text)
    match = re.fullmatch(r"(?s)\s*(.+?)\n[ \t]*overall_soundscape[ \t]*:[ \t]*(.+?)\n[ \t]*non_diegetic_music[ \t]*:[ \t]*(.+?)\s*", text)
    if names != ["overall_soundscape", "non_diegetic_music"] or not match or any(not value.strip() for value in match.groups()):
        invalid("the model omitted or malformed the Compact description, overall_soundscape or non_diegetic_music")
    return match.groups()


def compact_content(text):
    """Only section-heading removal is eligible for a Compact format correction."""
    description, soundscape, music = compact_parts(text)
    description = re.sub(r"(?mi)^\s*(?:subject_definitions|summary|retention_analysis|integrated_multimodal_description|detailed_description)\s*:[ \t]*", "", description)
    return tuple(re.sub(r"\s+", " ", value).strip() for value in (description, soundscape, music))

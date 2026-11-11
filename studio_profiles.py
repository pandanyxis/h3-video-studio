"""Adapt the pinned Continuity graph to the models shipped by this image."""
import hashlib
import json
import os
from pathlib import Path
import re
from copy import deepcopy

from studio_storage import atomic_json

PROFILES = Path("/ComfyUI/custom_nodes/ComfyUI-H3-Continuity-Studio/"
                "h3_continuity/timeline_api/profiles")
CONFIGURED = Path("/workspace/ComfyUI/user/__h3c_api/profiles/configured")
SELECTION_PREFIX = "studio-selection-"
MAX_LORAS = 8
MODEL_NAMES = {
    "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors":
        "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    "minimax_h3_video_vae_int8_convrot.safetensors":
        "minimax_h3_video_vae_fp16.safetensors",
    "minimax_h3_audio_vae_fp32.safetensors":
        "minimax_h3_audio_vae_fp32.safetensors",
    "MiniMax_H3_FL2VA_pruned_int8_convrot.safetensors":
        "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "minimax_h3_turbo_4step_ema_ckpt850_pruned_comfyui.safetensors":
        "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
}

def set_lora_stack(workflow, loras):
    graph, ui = workflow["api"], workflow["ui"]
    loader = graph["106"]
    nodes = {node["id"]: node for node in ui["nodes"]}
    ui_loader = nodes[106]
    links = {link[0]: link for link in ui["links"]}
    incoming = links[ui_loader["inputs"][0]["link"]]
    outgoing = links[ui_loader["outputs"][0]["links"][0]]
    target = graph[str(outgoing[3])]["inputs"]
    if not loras:
        target["model"] = loader["inputs"]["model"]
        nodes[incoming[1]]["outputs"][incoming[2]]["links"] = [outgoing[0]]
        outgoing[1:3] = incoming[1:3]
        ui["links"].remove(incoming)
        ui["nodes"].remove(ui_loader)
        del graph["106"]
        return

    first, *extra = loras
    loader["inputs"].update(lora_name=first[0], strength_model=first[1])
    ui_loader["widgets_values"][:2] = first
    if not extra:
        return
    previous = ui_loader
    next_node = max(ui.get("last_node_id", 0), *nodes)
    next_link = max(ui.get("last_link_id", 0), *links)
    for offset, (name, strength) in enumerate(extra, 1):
        next_node += 1
        next_link += 1
        node = deepcopy(ui_loader)
        node.update(id=next_node, pos=[ui_loader["pos"][0] + 360 * offset, ui_loader["pos"][1]],
                    order=max(n["order"] for n in ui["nodes"]) + 1)
        node["widgets_values"][:2] = [name, strength]
        node["inputs"][0]["link"] = next_link
        node["outputs"][0]["links"] = [outgoing[0]]
        previous["outputs"][0]["links"] = [next_link]
        ui["nodes"].append(node)
        ui["links"].append([next_link, previous["id"], 0, next_node, 0, "MODEL"])
        graph[str(next_node)] = deepcopy(loader)
        graph[str(next_node)]["inputs"].update(model=[str(previous["id"]), 0],
                                               lora_name=name, strength_model=strength)
        previous = node
    outgoing[1] = previous["id"]
    target["model"] = [str(previous["id"]), 0]
    ui.update(last_node_id=next_node, last_link_id=next_link)


def build_profile(base, profile_id, title, model, loras, steps):
    profile = json.loads(base)
    profile.update(id=profile_id, title=title)
    graph = profile["workflow"]["api"]
    graph["104"]["inputs"]["unet_name"] = model
    graph["114"]["inputs"]["steps"] = steps
    widgets = {104: {0: model}, 114: {1: steps}}
    for node in profile["workflow"]["ui"]["nodes"]:
        for index, value in widgets.get(node["id"], {}).items():
            node["widgets_values"][index] = value
    set_lora_stack(profile["workflow"], loras)
    return profile


def write_profile(base, profile_id, title, model, loras, steps):
    profile = build_profile(base, profile_id, title, model, loras, steps)
    atomic_json(PROFILES / f"{profile_id}.json", profile)


def validate_sampling(steps):
    if type(steps) is not int or not 1 <= steps <= 100:
        raise ValueError("A profile requires 1 to 100 sampling steps.")


def parse_loras(value, installed=None):
    if not isinstance(value, list) or len(value) > MAX_LORAS:
        raise ValueError(f"Choose up to {MAX_LORAS} LoRAs.")
    loras = []
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError("Each LoRA needs a file name.")
        name, strength = entry["name"], entry.get("strength")
        if installed is not None and name not in installed:
            raise ValueError("Select an installed LoRA.")
        if type(strength) not in (int, float) or not 0 <= strength <= 2:
            raise ValueError("Each LoRA requires a strength from 0 to 2.")
        loras.append((name, float(strength)))
    if len({name for name, _ in loras}) != len(loras):
        raise ValueError("Choose each LoRA only once.")
    return loras


def default_settings():
    profile = json.loads((PROFILES / "studio-turbo8.json").read_text(encoding="utf-8"))
    graph = profile["workflow"]["api"]
    return {
        "model": graph["104"]["inputs"]["unet_name"],
        "lora": graph["106"]["inputs"]["lora_name"],
        "steps": graph["114"]["inputs"]["steps"],
        "lora_strength": graph["106"]["inputs"]["strength_model"],
    }


def selection_profile(settings, options):
    if not isinstance(settings, dict):
        raise ValueError("Invalid render settings.")
    model, steps = settings.get("model"), settings.get("steps")
    validate_sampling(steps)
    if model not in options["models"]:
        raise ValueError("Select an installed model.")
    loras = parse_loras(settings.get("loras", [
        {"name": settings.get("lora"), "strength": settings.get("lora_strength")},
    ]), options["loras"])

    base = (PROFILES / "studio-turbo8.json").read_text(encoding="utf-8")
    profile = build_profile(base, "", "Custom render settings", model, loras, steps)
    content = json.dumps(profile, sort_keys=True, ensure_ascii=False)
    profile_id = SELECTION_PREFIX + hashlib.sha256(content.encode()).hexdigest()[:40]
    profile["id"] = profile_id
    path = CONFIGURED / f"{profile_id}.json"
    if not path.is_file():
        atomic_json(path, profile)
    return profile_id


def main():
    raw = (PROFILES / "native-turbo4.json").read_text(encoding="utf-8")
    for old, new in MODEL_NAMES.items():
        raw = raw.replace(json.dumps("minimax-h3\\" + old), json.dumps(new))
    graph = json.loads(raw)["workflow"]["api"]
    model = graph["104"]["inputs"]["unet_name"]
    lora = graph["106"]["inputs"]["lora_name"]
    write_profile(raw.replace("4-step", "8-step"), "studio-turbo8", "Studio H3 Turbo 8-step",
                  model, [(lora, 0.8)], 8)
    configured = json.loads(os.environ.get("STUDIO_RENDER_PROFILES") or "[]")
    if not isinstance(configured, list):
        raise ValueError("STUDIO_RENDER_PROFILES must be a JSON array.")
    for config in configured:
        name = config["id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,55}", name):
            raise ValueError("Use lowercase letters, digits, hyphens, or underscores in profile IDs.")
        steps = config.get("steps", 8)
        validate_sampling(steps)
        loras = parse_loras(config.get("loras", [
            {"name": config.get("lora", lora), "strength": config.get("lora_strength", 0.8)},
        ]))
        write_profile(raw, f"custom-{name}", config.get("title", name),
                      config.get("model", model), loras, steps)


if __name__ == "__main__":
    main()

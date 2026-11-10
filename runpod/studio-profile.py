"""Adapt the pinned Continuity graph to the models shipped by this image."""
import json
import os
from pathlib import Path
import re

PROFILES = Path("/ComfyUI/custom_nodes/ComfyUI-H3-Continuity-Studio/"
                "h3_continuity/timeline_api/profiles")
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

def write_profile(base, profile_id, title, model, lora, steps, strength):
    profile = json.loads(base)
    profile.update(id=profile_id, title=title)
    graph = profile["workflow"]["api"]
    graph["104"]["inputs"]["unet_name"] = model
    graph["106"]["inputs"].update(lora_name=lora, strength_model=strength)
    graph["114"]["inputs"]["steps"] = steps
    widgets = {104: {0: model}, 106: {0: lora, 1: strength}, 114: {1: steps}}
    for node in profile["workflow"]["ui"]["nodes"]:
        for index, value in widgets.get(node["id"], {}).items():
            node["widgets_values"][index] = value
    (PROFILES / f"{profile_id}.json").write_text(json.dumps(profile), encoding="utf-8")


def main():
    raw = (PROFILES / "native-turbo4.json").read_text(encoding="utf-8")
    for old, new in MODEL_NAMES.items():
        raw = raw.replace(json.dumps("minimax-h3\\" + old), json.dumps(new))
    graph = json.loads(raw)["workflow"]["api"]
    model = graph["104"]["inputs"]["unet_name"]
    lora = graph["106"]["inputs"]["lora_name"]
    write_profile(raw.replace("4-step", "8-step"), "studio-turbo8", "Studio H3 Turbo 8-step",
                  model, lora, 8, 0.8)
    configured = json.loads(os.environ.get("STUDIO_RENDER_PROFILES") or "[]")
    if not isinstance(configured, list):
        raise ValueError("STUDIO_RENDER_PROFILES moet een JSON-lijst zijn.")
    for config in configured:
        name = config["id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,55}", name):
            raise ValueError("Gebruik voor een profiel-ID kleine letters, cijfers, - of _.")
        steps = config.get("steps", 8)
        strength = config.get("lora_strength", 0.8)
        if type(steps) is not int or not 1 <= steps <= 100:
            raise ValueError("Een profiel vereist 1 tot 100 samplingstappen.")
        if type(strength) not in (int, float) or not 0 <= strength <= 2:
            raise ValueError("Een profiel vereist een LoRA-sterkte van 0 tot 2.")
        write_profile(raw, f"custom-{name}", config.get("title", name),
                      config.get("model", model), config.get("lora", lora), steps, strength)


if __name__ == "__main__":
    main()

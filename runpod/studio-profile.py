"""Adapt the pinned Continuity graph to the models shipped by this image."""
import json
from pathlib import Path

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


def main():
    raw = (PROFILES / "native-turbo4.json").read_text(encoding="utf-8")
    for old, new in MODEL_NAMES.items():
        raw = raw.replace(json.dumps("minimax-h3\\" + old), json.dumps(new))
    profile = json.loads(raw.replace("4-step", "8-step"))
    profile.update(id="studio-turbo8", title="Studio H3 Turbo 8-step")
    graph = profile["workflow"]["api"]
    graph["114"]["inputs"]["steps"] = 8
    graph["106"]["inputs"]["strength_model"] = 0.8
    for node in profile["workflow"]["ui"]["nodes"]:
        if node["id"] == 114:
            node["widgets_values"][1] = 8
        elif node["id"] == 106:
            node["widgets_values"][1] = 0.8
    (PROFILES / "studio-turbo8.json").write_text(json.dumps(profile), encoding="utf-8")


if __name__ == "__main__":
    main()

# Customize workflows in ComfyUI

Open a workflow under **Personal** in ComfyUI. Use the `text-to-video` variant for T2V and `image-to-video` for I2V. The filename also identifies the selected model, such as `minimax` or `eros`.

| Group | Controls |
|---|---|
| 1 · Inputs | Prompt, starting image for I2V, aspect ratio, and video duration |
| 2 · Models | Diffusion model, Qwen3-VL text encoder, and audio and video VAEs |
| 3 · LoRAs and preview | Style LoRAs and preview settings. The fixed Turbo LoRA belongs to the eight-step setup. |
| 4 · Generation | Combine inputs and models for video generation |
| 5 · Sampling | Seed, sampler, and sampling steps |
| 6 · Video output | Decode and combine audio and video, then save the result |

Use **Save As** to keep your own copy. A different checkpoint or LoRA may require different settings or a different workflow.

## Extra Civitai models

Add comma-separated identifiers to the pod or template.

| Environment variable | Destination |
|---|---|
| `CIVITAI_DIFFUSION_MODELS` | Diffusion models for the UNET loader |
| `CIVITAI_LORAS` | LoRAs |
| `CIVITAI_CHECKPOINTS` | Complete checkpoints for a checkpoint loader |

Use `version:123456` to select a version. For a specific quantization, use the Civitai URL with both `modelVersionId` and `fileId`. Set the token in `CIVITAI_TOKEN` or `civitai_token`.

The downloader verifies the SHA-256 from Civitai. Select the downloaded file in the appropriate loader. If it is missing from the list, refresh the model list or reload ComfyUI.

## Save your work

Download customized workflow JSON, videos, chats, and uploaded images before stopping or deleting a pod that uses temporary storage.

To ship a customized workflow with future pods, export its JSON, replace the matching `workflow-*.json` in this repository, and push to `main`. GitHub builds a new image for subsequent deployments.

`layout-workflows.py` reapplies the supplied layout to the six workflows. Back up custom workflows before using it. New node types need to be added to the layout script first.

## Free GPU memory for video

Set `ENABLE_QWEN=false` to run ComfyUI without the separate Qwen chat server. The video workflow still requires the Qwen3-VL text encoder. Higher resolutions and longer videos require more memory and compute time.

The image includes `combined-auto-unload`. After the last queued generation, including after an execution error, it asks ComfyUI to unload models and clear execution caches. Models stay loaded within a batch and reload for the next batch. This applies to all workflows without requiring extra canvas nodes.

# Pandanyxis - MiniMax + Qwen

Project owner and maintainer: [pandanyxis](https://github.com/pandanyxis).

Prebuilt ComfyUI T2V/I2V, Qwen vision API on one Runpod GPU.

## v1.0: temporary storage only

Image: `ghcr.io/pandanyxis/minimax-qwen-comfyui:comfy-v1.0`.
Use 200 GB container disk, no persistent/network/global mount, and CUDA 13.0 or newer on an RTX PRO 6000 96 GB or suitable Hopper/Blackwell GPU. The image targets SM 90, 100 and 120.

Each new Pod downloads the selected models into its own `/workspace`. The model files are not in the image. A process restart can reuse files still present on that disk; a new Pod downloads them again. There is no cloud model cache, upload process or separate storage subscription in this configuration. Temporary files can be lost when the Pod stops, resets or is replaced. Export chats, workflows, uploads and results that you want to retain.

The selected weights total approximately **96.75 GB**. The 200 GB container disk leaves room for staging, application state, extra models and generated outputs. Container disk bills while the Pod runs; no separate permanent volume is provisioned.

## Workflows and models

The personal T2V and I2V variants use either:

- Civitai MiniMax H3 FL2VA INT8 Pruned: model 2830065, version 3193337, file 3074134.
- H3 Eros Max beta6 INT8: model 2851079, version 3393047, file 3283276.

Exact filenames, sizes and SHA-256 hashes are pinned in `civitai-models.json`. The Mystic v5 FL2VA LoRA is downloaded and remains disabled until selected. Both workflows retain the Qwen3-VL text encoder, audio/video VAEs, TAE preview and 8-step Turbo LoRA. The independent Qwen chat model and vision projector are separate from the MiniMax text encoder.

The workflows use six left-to-right groups: **Invoer ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ Modellen ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ LoRAÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢s en preview ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ Generatie ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ Sampling ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ Video-uitvoer**. Change your prompt, image, resolution and duration in group 1. Add style LoRAs in group 3; use Save As for your own variants. `layout-workflows.py` reapplies this layout without changing model settings or node connections.

Use the explicitly named `workflow-{text,image}-to-video-civitai-{minimax,eros}-int8.json` variants. They are available in the personal workflow folders. Select your own input image for I2V. Notes are neutral workflow instructions; no upstream welcome/report workflow is generated. Existing edited personal copies are preserved by the personal installer.

## Optional Civitai downloads

Set comma-separated identifiers in the Pod/template environment:

| Variable | Model folder |
|---|---|
| `CIVITAI_CHECKPOINTS` | `models/checkpoints` |
| `CIVITAI_LORAS` | `models/loras` |
| `CIVITAI_DIFFUSION_MODELS` | `models/diffusion_models` |

`model:ID` selects the current primary file; `version:ID` pins a version's primary file. For a particular quantization, use a complete Civitai URL containing both `modelVersionId` and `fileId`. Do not repeat the built-in IDs in these fields. Keep credentials in `civitai_token` or `CIVITAI_TOKEN`, never in URLs. Additional files need compatible workflows and increase storage use.

## Credentials and access

| Variable | Purpose |
|---|---|
| `LLAMA_API_KEY` | Qwen API authentication, at least 32 random characters |
| `civitai_token` / `CIVITAI_TOKEN` | Required for Civitai downloads |
| `HF_TOKEN` | Hugging Face access where needed |
| `JUPYTER_TOKEN` | Jupyter authentication |

Use account-secret references such as `{{ RUNPOD_SECRET_YOUR_SECRET_NAME }}`. Credentials are not in the image or public repository. Generated defaults are saved privately in `/workspace/combined/access.json` and are temporary with the Pod. SSH uses registered account public keys.

Replace `POD_ID` in these URLs:

| Service | URL |
|---|---|
| ComfyUI | `https://POD_ID-8188.proxy.runpod.net` |
| Qwen API | `https://POD_ID-8080.proxy.runpod.net/v1` |
| Jupyter | `https://POD_ID-8888.proxy.runpod.net` |

Keep `ENABLE_COMFYUI=true`, `ENABLE_QWEN=true` and `PERSIST_MODELS_TO_VOLUME=false`. Set `ENABLE_QWEN=false` when generation needs the full GPU. The built-in video model selections are pinned in the model manifests.

## Your own runtime

The image starts from the official NVIDIA CUDA 13.0.3 Ubuntu 24.04 image. ComfyUI is installed directly from Comfy-Org with PyTorch 2.13.0 CUDA 13.0. The runtime and download scripts live in this repository. Only the three workflow node packs in `comfy-sources.json` are installed. Attention uses PyTorch's built-in implementation.

To continue developing, edit these files and push to `main`:

| File | Change |
|---|---|
| `comfy-sources.json` | Pinned ComfyUI and custom-node repositories |
| `civitai-models.json` | Built-in Civitai files and hashes |
| `model-manifest.json` | Expected sizes and SHA256 hashes |
| `provision-helpers.py` | Original helper-model repositories and revisions |
| `start-comfy.sh` | ComfyUI and Jupyter startup |
| `workflow-*.json` | Your T2V/I2V workflows |
| `Dockerfile` | Container dependencies and build |

Custom nodes and ComfyUI keep their own licenses in their source directories. This repository starts with a clean history and owns its container and startup scripts.

## Build and validation

GitHub Actions builds `Dockerfile`, runs unit tests and the ComfyUI CPU startup smoke test, then publishes `latest` and a commit-specific image tag. Model weights download at Pod startup; software installation and CUDA compilation happen during image build. The health check validates required file sizes, pinned Civitai download status and enabled service endpoints. A completed Pod startup check is not a video-generation benchmark.

Template changes apply to new Pods. Generate a template with `python make_template.py`; local `runtime-settings.json` may supply private environment settings. No storage mount is generated.

## Sources

- [NVIDIA CUDA image](https://hub.docker.com/r/nvidia/cuda).
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI) and the three repositories pinned in `comfy-sources.json`.
- [llama.cpp](https://github.com/ggml-org/llama.cpp), commit `f805c57a2d0b7cc171e599303ce2040f6e1bfe15`.
- Helper models: Comfy-Org/MiniMax-H3, lightx2v/Minimax-h3-Turbo, Kijai/MiniMax-H3-TAE.

Model weights are downloaded at startup and remain subject to their upstream licenses and access requirements. The independent image must pass its build and CPU startup checks before deployment; GPU video generation requires a separate real Pod test.

Startup logs label ComfyUI phases with `[comfy]` and Qwen phases with `[qwen]`. Hugging Face/Xet and Civitai downloads show transferred/total GiB, percentage, average MiB/s and estimated remaining seconds approximately every 15 seconds while transfer updates arrive. Hugging Face reports both file reconstruction and network transfer using its own byte counters; these can differ with Xet caching/compression. Missing totals or zero speed show an unknown total or calculating ETA. SHA-256 checks have a separate heartbeat. Service readiness is confirmed by server listening messages.

Civitai connection errors, timeouts, interrupted streams, HTTP 408/429 and temporary server errors retry with 5/10/20/40/60-second delays (then 60 seconds), keeping partial files for resume. Numeric Retry-After delays are respected up to 300 seconds. Authentication and invalid-file errors still fail visibly. Retry logs show a safe error category without signed URLs or credentials. If a pod fails immediately on Civitai metadata, check its network/DNS settings; Global Networking is unnecessary for these public downloads.

Version `comfy-v1.4` downloads the three selected Civitai files from the private model repository `Pandanyxis/comfyui-civitai-models`, pinned to commit `b102a78d81a88ef84b187528cd1f0f92362b3644`. Supply a read token for that repository as `HF_TOKEN`. All original filenames, sizes and SHA-256 checks remain unchanged. Extra user-selected Civitai files still use Civitai. Override `HF_CIVITAI_MIRROR_REPO` and `HF_CIVITAI_MIRROR_REVISION` for another verified mirror, or set the repository variable to an empty string to use Civitai. `mirror-to-hf.py` creates private copies from already downloaded verified files; it uses a separate `HF_WRITE_TOKEN` only for uploading. Never put the write token in a template or image.


New Runpod templates use `ghcr.io/pandanyxis/minimax-qwen-comfyui:latest`. GitHub Actions updates this tag only after the image checks and ComfyUI CPU smoke test pass. Create a new Pod to use the updated image; existing Pods are not updated automatically. Commit-specific image tags remain available.

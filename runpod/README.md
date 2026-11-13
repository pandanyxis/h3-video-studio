# Runpod GPU runtime

Runpod supplies one GPU for Qwen and MiniMax H3. The image also includes the Studio interface and the service that plans, writes, renders, and exports each project.

## Image and storage

Build from the repository root with `runpod/Dockerfile` or deploy the published image using a successful GitHub build's commit tag.

```sh
python runpod/make_template.py --image ghcr.io/pandanyxis/h3-video-studio:COMMIT_SHA
```

The script writes `runpod-template.json` for the Runpod REST API. Use an RTX PRO 6000 Blackwell with 96 GB VRAM and a CUDA 13-compatible driver. The template uses a 150 GB container disk with no persistent volume. The standard weights use about 70 GiB. The remaining space holds extra models, downloads, and projects.

Model downloads are verified on the pod. With temporary storage, Runpod erases models, projects, and films when the pod stops. Download any work you want to keep first. If you choose to attach a persistent volume at `/workspace`, a later start can reuse the verified files.

## Access

The Studio interface uses port `8190/http`. Set `STUDIO_PASSWORD` to at least 16 characters, either in the pod environment or through a Runpod Secret. Open **Connect → HTTP Service (8190)** and sign in as `studio`. Each new pod has a different HTTPS address.

Qwen and ComfyUI listen inside the pod. The `[qwen]` and `[comfy]` logs show download and verification progress. Under **Pods → your pod → Logs**, look for `[studio] Ready to render`. That message confirms Studio, Qwen, and the default render profile are available.

The runtime generates a local Qwen key and stores it privately under `/workspace`. Set `LLAMA_API_KEY` if you need a fixed key. Some optional downloads require `HF_TOKEN` or `CIVITAI_TOKEN`. Keep personal settings in an untracked `runtime-settings.json`. See [model configuration](RUNPOD-MODELS.md).

## GPU smoke test

Open Studio and render a short story with one reference image. Check that each planned shot has a prompt and a clip, each clip stays within fifteen seconds, and the exported MP4 plays. Stop the test pod when finished.

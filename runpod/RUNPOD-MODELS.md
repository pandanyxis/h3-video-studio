# Runpod model configuration

The default build uses Comfy-Org's MiniMax H3 FL2VA Pruned INT8 model with the existing text encoder, audio and video VAEs, Turbo LoRA, and preview model. Local Qwen writes the shot prompts. Eros, Mystic, and the private mirror are optional downloads.

The bundled T2V and I2V workflows use the Comfy-Org model by default. Existing personal workflows are preserved. On an existing volume, update the UNET loader if it points to a different model.

## Select a render profile

Choose a render profile and aspect ratio per project. **Automatic** follows the first reference image. The explicit sizes are 1024 × 576 for 16:9 and 576 × 1024 for 9:16.

Choose **Render profile → Custom render settings** to select an installed MiniMax H3 model and the sampling steps. Add up to eight compatible LoRAs with individual strengths from `-2` to `2`. Use the slider or enter an exact value. Studio applies LoRAs in the order shown. Remove all rows to render without a LoRA.

For presets, optionally provide a JSON array in `STUDIO_RENDER_PROFILES`. Use the exact filenames listed in ComfyUI.

```json
[{"id":"custom-h3","title":"Custom H3","model":"my-h3.safetensors","steps":8,"loras":[{"name":"turbo.safetensors","strength":0.8},{"name":"style.safetensors","strength":0.5}]}]
```

Existing presets using `lora` and `lora_strength` still work.

## Download extra models

Set `HF_TOKEN` or `CIVITAI_TOKEN` when the provider requires access. You can use Runpod Secrets for these values.

- Use `CIVITAI_CHECKPOINTS`, `CIVITAI_LORAS`, and `CIVITAI_DIFFUSION_MODELS` for comma-separated identifiers such as `model:123` or `version:456`.
- Use `HF_EXTRA_MODELS` for a JSON array such as `[{"repo":"owner/repo","revision":"COMMIT_SHA","file":"path/extra.safetensors","folder":"loras"}]`.

Use unique filenames. Studio reuses an existing verified file and needs space for the Hugging Face download cache.

Civitai files of at least 1 GiB use four connections by default when the download server supports byte ranges. Set `CIVITAI_DOWNLOAD_WORKERS` to a value from 1 to 8. The downloader verifies the SHA-256 of the complete file.

## Validate on Runpod

Build from the repository root using `runpod/Dockerfile` and pass the resulting image to `make_template.py --image`. Start on an RTX PRO 6000 Blackwell with 96 GB VRAM and empty model storage. Check the default downloads, render short T2V and I2V clips, and measure peak VRAM. Then test one Civitai extra and one Hugging Face extra. If you use a persistent volume, verify that a second start reuses the files.

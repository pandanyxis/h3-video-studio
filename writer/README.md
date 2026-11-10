# H3 Learning Writer — independent version

This local fork is **H3 Learning Writer 0.2.0-learning.1**, based on H3 Prompt Writer
Standalone 0.1.9. Original source and licensing are preserved in `upstream/`.
Source repository: [pandanyxis/H3-Learning-Writer](https://github.com/pandanyxis/H3-Learning-Writer).

Based on [H3 Prompt Writer](https://github.com/pandanyxis/ComfyUI-MiniMaxH3-Prompt-Writer).
The MIT license and original copyright notices are preserved in `LICENSE` and `upstream/LICENSE`.

## Learn from result videos

1. Run `start.bat`. Restart any older running instance and refresh the browser.
2. Connect RunPod through **Settings → API providers → Custom**, using your existing
   `/v1` URL, API key and `qwen-hauhau`. Enable image input.
3. Create a prompt using Single **Generate** or **Refine**.
4. Open **Learning Center** in the top toolbar, select the saved generation,
   and check the actual prompt used. Edit it if you changed your video prompt.
5. Select the connected vision model and upload the generated result video.
6. When analysis finishes, use **Copy Refine instruction** to copy the automatically
   suggested edit, then paste it into **Refine** on the matching prompt. Older analyses
   need a new upload to generate this field. No instruction is generated when the
   review finds no actionable prompt issue.

The analysis also shows whether learned instructions are currently active for the
writing model and mode. These instructions are applied automatically on subsequent
Generate and Refine requests while learning is enabled.

The app samples eight timestamped video frames and asks the model to compare them
with the brief, prompt and saved visual references. The uploaded video is deleted
after processing. Limits: 512 MB, 10 minutes, and up to 4096 × 4096 pixels.
Frames and references are sent to the selected API. Audio is not evaluated.

Video reviews prioritize anatomy and visual naturalness: hands, limbs, faces,
proportions, fused or duplicated body parts, contact, balance, warped geometry and
changes across sampled frames. Findings include timestamps, evidence, confidence
and severity. Intentional stylization, perspective, occlusion and blur should not
be mistaken for confirmed defects. A clean sample is not a guarantee that the
entire video is free of anatomical errors. Detailed findings appear in the version review.

Qwen proposes up to five conditional lessons. A second model request checks them
against up to five recent briefs and their existing system instructions. Approved
lessons are automatically added as supplemental system instructions to subsequent
Single generations and Refine requests using the same writing model and mode.
The original H3 contracts remain the foundation. Up to twelve lessons stay active
per writing model and mode. Reviews, lessons and validation reasons use English.

**This is an experimental self-learning instruction layer.** Validation uses the
same model and is a text review, not an A/B test with regenerated videos or proof
of better video quality. Model weights are not trained. Eight frames provide limited
motion evidence. Sequence and Music3 have no learning integration in this version.

View analyses and instruction versions in the Learning Center. Restore an approved
version, reset to the base instructions, or disable automatic application and updates.
Running analyses remain visible but do not activate new versions while learning is
disabled. Reconnect your API after restarting the app; API keys are not stored in
learning memory.

History, references, reviews and versions are saved locally in
`data/learning/learning.sqlite3`. Preserve this folder during upgrades. An upstream
ZIP may overwrite this fork's code; copy the project folder before upgrading.

## Requirements

- Windows with Python 3.12–3.15 (3.15 recommended), or uv. Run `start.bat` to create a local virtual environment. The launcher prefers Python 3.15 and installs prebuilt dependencies, including PyAV 19 for video processing. Existing environments with older PyAV versions are upgraded on launch.
- A connected image-capable API model for video reviews; RunPod Qwen is supported.
- API keys, model weights and llama.cpp executables are not bundled.
- The local app binds to 127.0.0.1. API keys remain in backend memory for the session.

## Validation

Local verification: `.venv/Scripts/python.exe -m unittest discover -s tests -v`.


## Learning Center cleanup

Use **Remove from list** to hide completed analyses and inactive instruction versions.
Use **Show removed items → Restore to list** to undo. The local learning database and
active instructions are preserved. Running analyses and active versions are protected.

## Repository contents

This repository contains application source, generic tests, a sample settings file,
and the bundled upstream code. Personal videos, references, API keys, learned rules,
local configuration, model weights and virtual environments are excluded.

## Image-to-Video reference fidelity

I2VA generation and refinement use a source-fidelity instruction covering facial
structure, hair, skin tone, body proportions, garment type, colors, patterns, fit,
materials, layers, footwear and accessories. Explicitly requested changes take
priority. Unseen details must not be invented.

Result reviews inventory visible source features, compare them with sampled video
frames, and report expected versus observed details, timestamps, confidence,
severity and whether a change was requested. Lighting, perspective, occlusion and
motion blur are considered before reporting a defect. Findings are saved as
`reference_fidelity` in the analysis review and contribute to its summary.

This update does not change your selected external model or add content filtering.
The external provider's own behavior and restrictions still apply.

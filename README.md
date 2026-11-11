# H3 Video Studio

Turn a story and one to nine reference images into a film with MiniMax H3. The Studio browser interface is in `studio.html` and the project service is in `studio_web.py`.

The local Qwen planner chooses one to twenty shots and assigns a duration to each. H3 rounds durations to its native frame grid and each clip stays within ten seconds. The Studio Writer then writes an H3 prompt for each shot with movement, framing, sound, and opening and closing states. Both steps are implemented in `studio_writer.py`. ComfyUI and H3 Continuity render the clips on the Runpod GPU. See [Runpod model configuration](runpod/RUNPOD-MODELS.md).

## Start the GPU host

GitHub Actions builds `runpod/Dockerfile` from the repository root and publishes it to `ghcr.io/pandanyxis/h3-video-studio`. Use a commit tag to deploy a specific build.

```sh
python runpod/make_template.py --image ghcr.io/pandanyxis/h3-video-studio:COMMIT_SHA
```

Use an RTX PRO 6000 Blackwell with 96 GB VRAM and CUDA 13 or newer. The template allocates 150 GB of temporary container storage. A private image requires read access through Runpod Container Registry Auth. See the [Runpod setup guide](runpod/README.md).

Set `STUDIO_PASSWORD` to a password of at least 16 characters. Open **Connect → HTTP Service (8190)** and sign in as `studio`. Each pod has its own HTTPS URL.

Add a story and reference images, then click **Make my film**. Add optional notes to each image to identify people, objects, locations, or the intended use of that reference. The Planner, Writer, and clip review use those notes. The project page shows the active step, individual clips as they become available, and the finished MP4. Under **Review clips**, open a shot and use **Refine clip** to draft a revision from sampled frames and your feedback. Review or edit the proposed prompt before choosing **Render new take**. Studio saves the result as a separate film version with access to the previous take. Use **Stop production** or **Stop all productions** to request cancellation. **Clear projects** deletes saved Studio projects and exports after all productions stop.

After rendering, Studio automatically asks Qwen to compare frames from the end of each clip with frames from the start of the next, taking planned cuts into account. Open the incoming shot under **Review clips** to see the verdict and add any suggestion to **Refine clip** before deciding whether to render a new take. An unavailable review still allows the film to export.

Choose **Review all clips** to review the completed film in one pass. Add optional feedback for Qwen to focus on. The review uses the original story, shot plan, sampled frames from every clip, and any available transition reports. It saves a summary and actionable notes you can copy into **Refine clip**.

Studio saves the shot plan, prompts, and render request for recovery. If a render is interrupted, open its production log and resume the project. Recovery requires the project files to remain on disk. Temporary storage is erased when the pod stops, so download your films and clips first.

## Run over SSH

```sh
python /opt/studio/run_studio.py story.txt /workspace/studio/projects/test reference-a.png
```

The project directory contains the shot plan, timeline, render ID, individual clips, and `film.mp4`. Browser projects also save the original input and `render.log`. Reuse the same project directory to check an existing render and finish its export. Use a new directory for a different story.

## Preview the interface locally

```sh
python -m pip install -r requirements.txt
python studio_web.py --projects ./projects
```

Open [local Studio](http://localhost:8190). Use the Runpod image for full production.

## Checks

```sh
python -m pip install -r requirements.txt requests tqdm
PYTHONPATH=.:runpod python -m unittest discover -s tests
PYTHONPATH=.:runpod python -m unittest discover -s runpod/tests
```

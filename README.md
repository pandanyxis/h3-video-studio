# H3 Video Studio

Turn a story and one to nine reference images into a film with MiniMax H3. The Studio browser interface is in `studio.html` and the project service is in `studio_web.py`.

The local Qwen planner chooses one to twenty shots and assigns a duration to each. H3 rounds durations to its native frame grid and each clip stays within fifteen seconds. The Studio Writer then writes an H3 prompt for each shot with movement, framing, sound, and opening and closing states. Both steps are implemented in `studio_writer.py`. ComfyUI and H3 Continuity render the clips on the Runpod GPU. See [Runpod model configuration](runpod/RUNPOD-MODELS.md).

## Start the GPU host

GitHub Actions builds `runpod/Dockerfile` from the repository root and publishes it to `ghcr.io/pandanyxis/h3-video-studio`. Use a commit tag to deploy a specific build.

```sh
python runpod/make_template.py --image ghcr.io/pandanyxis/h3-video-studio:COMMIT_SHA
```

Use an RTX PRO 6000 Blackwell with 96 GB VRAM and CUDA 13 or newer. The template allocates 150 GB of temporary container storage. A private image requires read access through Runpod Container Registry Auth. See the [Runpod setup guide](runpod/README.md).

Set `STUDIO_PASSWORD` to a password of at least 16 characters. Open **Connect → HTTP Service (8190)** and sign in as `studio`. Each pod has its own HTTPS URL.

Add a story and reference images, then click **Make my film**. Add optional notes to each image to identify people, objects, locations, or the intended use of that reference. The Planner, Writer, and clip review use those notes. The project page shows the active step, individual clips as they become available, and the finished MP4. Under **Review clips**, open a shot and use **Refine clip** to draft a revision from sampled frames and your feedback. Review or edit the proposed prompt before choosing **Render new take**. Studio saves the result as a separate film version with access to the previous take. Use **Stop production** or **Stop all productions** to request cancellation. **Clear projects** deletes saved Studio projects and exports after all productions stop.

For new productions, Studio renders the shots in order. Before rendering the next shot, Qwen compares sampled frames and the exact last frame of the completed clip with the story, the next shot's planned opening, and its prompt. If needed, Studio adjusts the next prompt while retaining its story beat, duration, planned cut, and reference labels. Each review uses the original reference images and notes. Open the incoming shot to see the review. If a review is interrupted, resume the project to retry it.

Studio writes new dialogue in English by default and preserves any lines you supply verbatim. You can specify another spoken language in the story. Studio prompts H3 to align spoken words with the character's mouth movements. Native speech quality and timing depend on H3, and the frame review uses still images.

After rendering, Studio automatically asks Qwen to compare frames from the end of each clip with frames from the start of the next, taking planned cuts into account. Open the incoming shot under **Review clips** to see the verdict, play the cut from the exported film with **Watch transition**, or add a suggestion to **Refine clip**. An unavailable review at this stage still allows the film to export.

Choose **Review all clips** to review the completed film in one pass. Add optional feedback for Qwen to focus on. The review uses the original story, shot plan, sampled frames from every clip, and any available transition reports. It saves a summary and an assessment for each shot. Use **Apply changes and render** to render the proposed changes together as a new film version. Individual **Refine clip** reviews also include the previous clip's last frame and the next clip's first frame when available.

Studio saves the shot plan, prompts, and render request for recovery. If a render is interrupted, open its production log and resume the project. Recovery requires the project files to remain on disk. Temporary storage is erased when the pod stops, so download your films and clips first.

## Run over SSH

```sh
python /opt/studio/run_studio.py story.txt /workspace/studio/projects/test reference-a.png
```

The project directory contains the shot plan, timeline, render ID, individual clips, and `film.mp4`. Browser projects also save the original input and `render.log`. Reuse the same project directory to check an existing render and finish its export. Use a new directory for a different story.

## Local MCP on Runpod

The Runpod image starts a Streamable HTTP MCP server at `http://127.0.0.1:8191/mcp` alongside Studio. Point an MCP client on the pod at that URL, or reach it through an SSH local port forward. The MCP tools list productions, show live clip review and render progress, and send stop or resume requests to the same Studio queue. Studio continues the shot-by-shot workflow when the MCP client disconnects.

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

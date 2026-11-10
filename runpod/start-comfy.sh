#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "[comfy] FAILED at startup line $LINENO (exit $?)" >&2' ERR
export PYTHONUNBUFFERED=1
echo "[comfy] Step 1/4: downloading and verifying Hugging Face helper models"
/usr/bin/python3 /opt/combined/prepare-comfy.py
/opt/hf/bin/python /opt/combined/provision-helpers.py
echo "[comfy] Step 2/4: downloading requested Hugging Face extras"
/opt/hf/bin/python /opt/combined/download-hf-extras.py
echo "[comfy] Step 3/4: preparing extra models"
for pair in CIVITAI_CHECKPOINTS:checkpoints CIVITAI_LORAS:loras CIVITAI_DIFFUSION_MODELS:diffusion_models; do
  key="${pair%%:*}"
  folder="${pair#*:}"
  IFS=',' read -ra identifiers <<< "${!key:-}"
  for identifier in "${identifiers[@]}"; do
    [[ -z "${identifier// /}" ]] && continue
    /opt/venv/bin/python /opt/combined/download-civitai.py \
      --identifier "${identifier// /}" --output "/workspace/ComfyUI/models/$folder"
  done
done

echo "[comfy] Step 4/4: starting Jupyter (8888) and ComfyUI (8188); waiting for their ready messages"
cd /ComfyUI
pids=()
cleanup() {
  trap - EXIT TERM INT
  for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
# The token is inherited through the environment, never process arguments.
export JUPYTER_TOKEN
/opt/venv/bin/jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root \
  --ServerApp.root_dir=/workspace &
pids+=("$!")
/opt/venv/bin/python main.py --listen 0.0.0.0 --port 8188 \
  --disable-dynamic-vram --preview-method auto \
  --base-directory /workspace/ComfyUI \
  --extra-model-paths-config /workspace/combined/model-paths.yaml &
pids+=("$!")
set +e
wait -n "${pids[@]}"
status=$?
set -e
(( status == 0 )) && status=1
exit "$status"

# syntax=docker/dockerfile:1
ARG BASE_IMAGE=nvidia/cuda:13.0.3-cudnn-devel-ubuntu24.04@sha256:0230b7f243483cb15969fa3cc724a9459599604427052fc2a0d4291c7c0647dd
FROM ${BASE_IMAGE} AS builder
RUN apt-get update && apt-get install -y --no-install-recommends git cmake ninja-build build-essential libcurl4-openssl-dev && rm -rf /var/lib/apt/lists/*
ARG LLAMA_REF=f805c57a2d0b7cc171e599303ce2040f6e1bfe15
ARG CUDA_ARCHITECTURES=90;100;120
ARG BUILD_JOBS=2
RUN git init /src/llama && cd /src/llama && git remote add origin https://github.com/ggml-org/llama.cpp.git && git fetch --depth 1 origin ${LLAMA_REF} && git checkout --detach FETCH_HEAD
# CI has no GPU driver. Use the CUDA toolkit stub only while building.
RUN mkdir -p /opt/cuda-link \
    && test -f /usr/local/cuda/lib64/stubs/libcuda.so \
    && ln -s /usr/local/cuda/lib64/stubs/libcuda.so /opt/cuda-link/libcuda.so.1
RUN cmake -S /src/llama -B /src/llama/build -G Ninja \
    -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}" \
    -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_TESTS=OFF -DGGML_NATIVE=OFF \
    -DCMAKE_INSTALL_PREFIX=/opt/llama -DCMAKE_INSTALL_RPATH=/opt/llama/lib \
    -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath-link,/opt/cuda-link" \
    && cmake --build /src/llama/build --target llama-server -j ${BUILD_JOBS} \
    && mkdir -p /opt/llama/bin /opt/llama/lib \
    && cp /src/llama/build/bin/llama-server /opt/llama/bin/ \
    && find /src/llama/build -name '*.so*' -exec cp -a {} /opt/llama/lib/ \;

FROM ${BASE_IMAGE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3-venv python3-dev git curl libcurl4t64 libgomp1 openssh-server ffmpeg \
    libgl1 libglib2.0-0 build-essential \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_*
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/cu130
ENV PIP_CONSTRAINT=/opt/combined/torch-constraints.txt
COPY runpod/torch-constraints.txt runpod/comfy-sources.json runpod/bake-comfy.py /opt/combined/
RUN /opt/venv/bin/python /opt/combined/bake-comfy.py \
    && /opt/venv/bin/pip install --no-cache-dir jupyterlab==4.4.10 requests==2.32.5
# Dedicated environment for Hugging Face downloads.
RUN /usr/bin/python3 -m venv /opt/hf \
    && env -u PIP_CONSTRAINT /opt/hf/bin/pip install --no-cache-dir huggingface_hub==2.0.0 hf-xet==1.6.0
COPY --from=builder /opt/llama /opt/llama
COPY runpod/start-qwen runpod/start-services.sh /usr/local/bin/
COPY runpod/supervisor.py runpod/healthcheck.py runpod/bootstrap.py runpod/start-prebuilt.py runpod/cpu-smoke.py /opt/combined/
COPY runpod/start-comfy.sh runpod/prepare-comfy.py /opt/combined/
COPY runpod/auto-unload/ /ComfyUI/custom_nodes/combined-auto-unload/
COPY runpod/workflow-text-to-video.json runpod/workflow-image-to-video.json /opt/combined/
COPY runpod/download-civitai.py runpod/provision-helpers.py runpod/download-hf-extras.py /opt/combined/
COPY runpod/model-manifest.json runpod/download_progress.py runpod/download-qwen.py /opt/combined/
RUN --mount=from=builder,source=/opt/cuda-link,target=/opt/cuda-link \
    chmod +x /usr/local/bin/start-qwen /usr/local/bin/start-services.sh \
    && bash -n /usr/local/bin/start-qwen /usr/local/bin/start-services.sh /opt/combined/start-comfy.sh \
    && LD_LIBRARY_PATH=/opt/cuda-link:/opt/llama/lib:$LD_LIBRARY_PATH /opt/llama/bin/llama-server --version
COPY requirements.txt /opt/studio/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /opt/studio/requirements.txt
COPY plan_story.py prepare_timeline.py export_project.py run_studio.py studio_writer.py studio_storage.py studio_profiles.py studio_render.py studio_refine.py studio_web.py studio.html /opt/studio/
COPY runpod/h3-verified-preview.patch /opt/combined/
RUN git -C /ComfyUI/custom_nodes/ComfyUI-H3-Continuity-Studio apply /opt/combined/h3-verified-preview.patch \
    && python3 /opt/studio/studio_profiles.py
ENV PATH=/opt/venv/bin:$PATH
ENV ENABLE_STUDIO=true ENABLE_COMFYUI=true ENABLE_QWEN=true CTX_SIZE=65536 \
    COMFY_START_SCRIPT=/opt/combined/start-comfy.sh \
    HF_HOME=/workspace/qwen-runpod/hf-cache HF_XET_HIGH_PERFORMANCE=1 \
    HF_XET_NUM_CONCURRENT_RANGE_GETS=32 HF_HUB_DOWNLOAD_TIMEOUT=60 HF_HUB_DISABLE_UPDATE_CHECK=1
WORKDIR /workspace
EXPOSE 8190 8188 8080 8888 22
HEALTHCHECK --interval=30s --timeout=15s --start-period=90m --retries=3 CMD ["/usr/bin/python3", "/opt/combined/healthcheck.py"]
ENTRYPOINT ["/opt/nvidia/nvidia_entrypoint.sh"]
CMD ["/usr/bin/python3", "-u", "/opt/combined/start-prebuilt.py"]

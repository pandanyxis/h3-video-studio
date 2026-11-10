#!/usr/bin/env bash
set -euo pipefail
mkdir -p /workspace/studio/writer-data
if [[ ! -L /opt/writer/data ]]; then
  ln -s /workspace/studio/writer-data /opt/writer/data
fi
cd /opt/writer
exec /opt/writer-venv/bin/python -m h3_standalone --no-browser --fixed-port --port 8190

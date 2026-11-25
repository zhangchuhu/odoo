#!/bin/sh
set -eu
worker_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
project_root=$(CDPATH= cd -- "$worker_dir/../../.." && pwd -P)
export PYTHONPATH="$project_root/deploy/mercado"
exec "$project_root/.venv/bin/python" -m optimization_worker.native_collector_host "$@"

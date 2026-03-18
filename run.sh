#!/bin/bash
# Wrapper to run mkvideo in the mkvideo conda environment
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate mkvideo conda env and run
/Users/monmon/miniconda3/envs/mkvideo/bin/python "$SCRIPT_DIR/main.py" "$@"

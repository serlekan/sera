#!/usr/bin/env sh
set -eu
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
repo_root=$(CDPATH= cd "$script_dir/.." && pwd)
python3 -m pip install --user --no-build-isolation "$repo_root"
printf '%s\n' "Installed. Run: sera --version"

#!/bin/sh

# set -euo pipefail
# IFS=$'\n\t'

echo exec pipenv run python3 main.py "$@"
cd /app && exec pipenv run python3 main.py "$@"

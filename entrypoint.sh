#!/bin/sh

set -euo pipefail
IFS=$'\n\t'

exec python3 /main.py "$@"

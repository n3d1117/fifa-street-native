#!/bin/sh
# Every successful checkpoint must immediately reach the private remote.
set -eu
python3 scripts/check-repo-hygiene.py --staged
git commit "$@"
git push origin HEAD

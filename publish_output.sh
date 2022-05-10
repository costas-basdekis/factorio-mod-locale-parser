#!/usr/bin/env bash

set -eox pipefail

cp main.py /tmp/
git fetch --depth=1
git switch output
git clean -f -d
cp /tmp/main.py .
./main.py
git add ./*.json index.html cache
if git diff --cached --exit-code >/dev/null ; then
  echo "No changes to commit"
  exit 0
fi
git commit -m "Update output"
git push

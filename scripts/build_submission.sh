#!/usr/bin/env bash
# Assembles agent/ + the compiled cg engine bindings into build/submission.tar.gz,
# ready to upload as a Kaggle competition submission.
set -euo pipefail

cd "$(dirname "$0")/.."

CG_SRC="data/sample_submission/sample_submission/cg"
if [ ! -d "$CG_SRC" ]; then
  echo "error: $CG_SRC not found — download competition data first:" >&2
  echo "  kaggle competitions download -c pokemon-tcg-ai-battle -p data --unzip" >&2
  exit 1
fi

rm -rf build
mkdir -p build
cp agent/main.py build/main.py
cp agent/deck.csv build/deck.csv
cp -r "$CG_SRC" build/cg

(cd build && tar -czvf ../build/submission.tar.gz main.py deck.csv cg)

echo "Wrote build/submission.tar.gz"

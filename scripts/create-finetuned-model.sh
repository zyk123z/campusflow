#!/usr/bin/env bash
set -euo pipefail

MODEL_NAME="${OLLAMA_MODEL:-qwen2.5:7b}"

DEFAULT_OLLAMA_BIN="$(command -v ollama || true)"
if [ -z "$DEFAULT_OLLAMA_BIN" ] && [ -x "/Applications/Ollama.app/Contents/Resources/ollama" ]; then
  DEFAULT_OLLAMA_BIN="/Applications/Ollama.app/Contents/Resources/ollama"
fi
OLLAMA_BIN="${OLLAMA_BIN:-$DEFAULT_OLLAMA_BIN}"

if [ ! -x "$OLLAMA_BIN" ]; then
  echo "Cannot find Ollama."
  echo "Install Ollama or set OLLAMA_BIN to the ollama executable path."
  exit 1
fi

echo "Preparing CampusFlow base model: $MODEL_NAME"
"$OLLAMA_BIN" pull "$MODEL_NAME"
echo "Run CampusFlow with: AI_PROVIDER=ollama OLLAMA_MODEL=$MODEL_NAME ./scripts/run-dev.sh"

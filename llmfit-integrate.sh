#!/bin/bash
# Merge custom llmfit models into an llmfit checkout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_DB="${LLMFIT_CUSTOM_DB:-$HOME/.cache/llmfit/custom_hf_models.json}"

usage() {
  cat <<'EOF'
Usage: ./llmfit-integrate.sh [llmfit-repo-path] [--db /path/to/custom_hf_models.json]

Merges custom models into llmfit-core/data/hf_models.json and mirrors the result to
data/hf_models.json when that file exists.
EOF
}

LLMFIT_REPO=""
CUSTOM_DB="$DEFAULT_DB"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)
      CUSTOM_DB="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$LLMFIT_REPO" ]]; then
        LLMFIT_REPO="$1"
      else
        echo "Unexpected argument: $1" >&2
        usage >&2
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$LLMFIT_REPO" ]]; then
  echo "Missing llmfit checkout path." >&2
  usage >&2
  exit 1
fi

LLMFIT_DATA_FILE="$LLMFIT_REPO/llmfit-core/data/hf_models.json"
LEGACY_DATA_FILE="$LLMFIT_REPO/data/hf_models.json"

echo "llmfit custom model integration"
echo "  repo: $LLMFIT_REPO"
echo "  custom db: $CUSTOM_DB"
echo

if [[ ! -f "$CUSTOM_DB" ]]; then
  echo "No custom models found at $CUSTOM_DB" >&2
  echo "Run: python3 llmfit-model-adder.py add <repo-id>" >&2
  exit 1
fi

if [[ ! -d "$LLMFIT_REPO" ]]; then
  echo "llmfit repo not found at $LLMFIT_REPO" >&2
  echo "Clone or point this script at an llmfit checkout first." >&2
  exit 1
fi

if [[ ! -f "$LLMFIT_DATA_FILE" ]]; then
  echo "llmfit data file not found at $LLMFIT_DATA_FILE" >&2
  exit 1
fi

python3 - "$CUSTOM_DB" "$LLMFIT_DATA_FILE" "$LEGACY_DATA_FILE" <<'PYEOF'
import json
import re
import sys
from pathlib import Path

custom_db = Path(sys.argv[1])
target_file = Path(sys.argv[2])
legacy_file = Path(sys.argv[3])

QUANT_PATTERN = re.compile(
    r"(?:-|\.)(?:F32|F16|BF16|(?:IQ)?Q[0-9]+(?:_[A-Z0-9]+)+)(?:\.gguf)?$",
    re.IGNORECASE,
)


def canonical_model_family(name: str) -> str:
    family = name.strip()
    if family.lower().endswith(".gguf"):
        family = family[:-5]
    if family.upper().endswith("-GGUF"):
        family = family[:-5]
    match = QUANT_PATTERN.search(family)
    if match:
        family = family[: match.start()]
    return family.lower()


with custom_db.open() as handle:
    custom_payload = json.load(handle)

with target_file.open() as handle:
    existing_models = json.load(handle)

custom_models = custom_payload.get("models", [])
family_keys = {canonical_model_family(model["name"]) for model in custom_models}
custom_names = {model["name"] for model in custom_models}

filtered_models = []
removed = 0
for model in existing_models:
    family = canonical_model_family(model["name"])
    # The integration step mirrors the Python adder's replacement rule so the
    # staged custom DB and the target llmfit checkout cannot drift semantically.
    if family in family_keys or model["name"] in custom_names:
        removed += 1
        continue
    filtered_models.append(model)

merged = []
for model in custom_models:
    normalized = dict(model)
    normalized.pop("_custom", None)
    merged.append(normalized)

result = sorted(filtered_models + merged, key=lambda item: item["name"].lower())

with target_file.open("w") as handle:
    json.dump(result, handle, indent=2)
    handle.write("\n")

if legacy_file.exists():
    with legacy_file.open("w") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")

print(f"Loaded {len(custom_models)} custom model entrie(s)")
print(f"Removed {removed} stale entrie(s) from the target database")
print(f"Wrote {len(result)} total entrie(s) to {target_file}")
if legacy_file.exists():
    print(f"Mirrored merged output to {legacy_file}")
PYEOF

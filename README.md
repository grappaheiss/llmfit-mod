# llmfit-mod

Import custom Hugging Face GGUF repos, normalize them into llmfit-style metadata, and merge them into an `llmfit` checkout without manual JSON edits.

This repository is a small public proof/workflow repo, not a full replacement for upstream `llmfit`.

## Canonical Entry

Use `llmfit-model-adder.py` as the public CLI entrypoint.

`llmfit-model-adder_v2.py` is only a compatibility wrapper and should not be the documented path.

## What This Repo Does

- expands one Hugging Face repo into one or more quantized llmfit entries
- replaces stale entries for the same model family on repeated runs
- merges the staged entries into `llmfit-core/data/hf_models.json`
- lets you rebuild and activate `llmfit` so the compiled binary can search those new models

## Public Repo Boundary

Included here:

- `llmfit-model-adder.py`
- `llmfit-integrate.sh`
- `examples/`
- this `README.md`

Not included here:

- a vendored `llmfit-source` checkout
- upstream git history
- build outputs and local caches
- internal PM/task artifacts

Bring your own `llmfit` checkout and point the integration script at it.

## Validated Flow

This is the flow that was validated on the `popos` environment:

```bash
cd /path/to/llmfit-mod

# 1. Add a model family to the local custom DB
python3 llmfit-model-adder.py add Ex0bit/MiniMax-M2.1-PRISM

# 2. Inspect staged custom entries
python3 llmfit-model-adder.py list

# 3. Merge into your llmfit checkout
./llmfit-integrate.sh /path/to/llmfit-source

# 4. Rebuild the llmfit binary that uses the merged JSON
source ~/.cargo/env
cd /path/to/llmfit-source
cargo build --release

# 5. Replace the active installed binary if you launch llmfit by name
sudo cp target/release/llmfit /usr/local/bin/llmfit

# 6. Verify
llmfit search MiniMax-M2.1-PRISM

# Or verify against the rebuilt binary directly
./target/release/llmfit search MiniMax-M2.1-PRISM
```

`cargo build --release` only updates `target/release/llmfit`.

It does not automatically replace `/usr/local/bin/llmfit`.

If you launch `llmfit` by name and want the TUI/CLI to reflect the rebuilt model database, you must also run:

```bash
sudo cp target/release/llmfit /usr/local/bin/llmfit
```

Expected active install path:

```bash
/usr/local/bin/llmfit
```

That is the path plain `llmfit` should resolve to on a standard setup where `/usr/local/bin` is on `PATH`.

## Prerequisites

- Python 3
- a local `llmfit` checkout
- Rust toolchain available for rebuilding `llmfit`
- permission to replace `/usr/local/bin/llmfit` if you want plain `llmfit` to point at the rebuilt binary

Example upstream checkout setup:

```bash
git clone https://github.com/AlexsJones/llmfit.git /path/to/llmfit-source
```

## Why You May Not See the Model in TUI

The most common reason is binary mismatch.

`llmfit-integrate.sh` updates the checkout at `/path/to/llmfit-source`, and `cargo build --release` builds a binary inside that same checkout. But if you then run plain:

```bash
llmfit
```

your shell may launch an older system-installed binary instead of:

```bash
/path/to/llmfit-source/target/release/llmfit
```

Check which binary you are using:

```bash
which llmfit
```

Expected result:

```bash
/usr/local/bin/llmfit
```

Check the rebuilt binary directly:

```bash
/path/to/llmfit-source/target/release/llmfit search MiniMax-M2.1-PRISM
```

If that works, the add/merge/rebuild flow succeeded and your installed binary is the thing that is stale.

To activate the rebuilt binary for plain `llmfit`, run:

```bash
sudo cp /path/to/llmfit-source/target/release/llmfit /usr/local/bin/llmfit
```

If you do not want to install it system-wide, launch the rebuilt binary by full path instead:

```bash
/path/to/llmfit-source/target/release/llmfit
```

## Quick Start

```bash
# Add or replace one model family in the custom database
python3 llmfit-model-adder.py add bartowski/Llama-3.1-8B-Instruct-GGUF

# Inspect the staged custom entries
python3 llmfit-model-adder.py list

# Merge them into your llmfit checkout
./llmfit-integrate.sh /path/to/llmfit-source

# Rebuild and verify with the rebuilt binary
source ~/.cargo/env
cd /path/to/llmfit-source
cargo build --release
sudo cp target/release/llmfit /usr/local/bin/llmfit
llmfit search Llama-3.1-8B-Instruct
./target/release/llmfit search Llama-3.1-8B-Instruct
```

The merge target is `llmfit-core/data/hf_models.json`. If `data/hf_models.json` also exists in the checkout, the script mirrors the merged output there for repo consistency.

## CLI Contract

```bash
# Add all discovered quantizations
python3 llmfit-model-adder.py add <repo-id>

# Keep only specific quantizations in the custom database
python3 llmfit-model-adder.py add <repo-id> --quants Q4_K_M Q6_K

# Inspect the staged custom database
python3 llmfit-model-adder.py list [--db /path/to/custom_hf_models.json]

# Export normalized entries without mutating the local custom database
python3 llmfit-model-adder.py export <repo1> <repo2> --output models.json

# Merge a prepared JSON payload into the local custom database
python3 llmfit-model-adder.py merge --input models.json [--db /path/to/custom_hf_models.json]
```

The custom database defaults to `~/.cache/llmfit/custom_hf_models.json`. Override it with `--db` on the Python CLI or `LLMFIT_CUSTOM_DB` / `--db` on the integration script.

## Troubleshooting

### I rebuilt but still do not see the model in TUI

Run these in order:

```bash
which llmfit
/path/to/llmfit-source/target/release/llmfit search <model-fragment>
python3 llmfit-model-adder.py list
```

If the rebuilt binary finds the model, the issue is not the adder or merge step. The issue is that your shell is launching a different binary.

Fix it with:

```bash
sudo cp /path/to/llmfit-source/target/release/llmfit /usr/local/bin/llmfit
```

Then confirm:

```bash
which llmfit
```

It should print:

```bash
/usr/local/bin/llmfit
```

### How do I confirm the model made it into the merged JSON?

```bash
python3 - <<'PY'
import json
data = json.load(open('/path/to/llmfit-source/llmfit-core/data/hf_models.json'))
for m in data:
    if 'minimax-m2.1-prism' in m['name'].lower():
        print(m['name'], '->', m.get('quantization'))
PY
```

### What did the validated MiniMax run produce?

The validated remote run produced exactly these three staged and merged entries:

- `Ex0bit/MiniMax-M2.1-PRISM-Q1_S`
- `Ex0bit/MiniMax-M2.1-PRISM-Q2_M`
- `Ex0bit/MiniMax-M2.1-PRISM-Q4_NL`

## Sample Artifact Set

The `examples/` directory contains a trust-building sample set:

- `sample-custom-models.json`
- `sample-hf-models-before.json`
- `sample-hf-models-after.json`

Use those files to validate the merge path without live Hugging Face access.

## Public Boundary

Public release scope:

- `README.md`
- `llmfit-model-adder.py`
- `llmfit-integrate.sh`
- `examples/`

Keep private or exclude before publishing:

- `__pycache__/`, `.pytest_cache/`, and local caches
- vendored upstream checkout data and git history
- machine-specific helpers that are not part of the public demo path

The local `.gitignore` in this folder encodes the exclusion strategy for those non-public artifacts.

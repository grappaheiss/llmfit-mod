#!/usr/bin/env python3
"""
Canonical public entrypoint for adding custom Hugging Face GGUF models to llmfit.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

try:
    import urllib.request
except ImportError:
    print("Error: urllib is required (Python 3 standard library)")
    sys.exit(1)

HF_API = "https://huggingface.co/api/models"

QUANT_BPP = {
    "F32": 4.0,
    "F16": 2.0,
    "BF16": 2.0,
    "Q8_0": 1.0,
    "Q6_K": 0.75,
    "Q5_K_M": 0.625,
    "Q5_K_S": 0.5625,
    "Q4_K_M": 0.5,
    "Q4_0": 0.5,
    "Q4_K_S": 0.4375,
    "Q3_K_L": 0.4375,
    "Q3_K_M": 0.4375,
    "Q3_K_S": 0.375,
    "Q2_K": 0.3125,
    "IQ4_XS": 0.4375,
    "IQ4_XL": 0.4375,
    "IQ3_S": 0.375,
    "IQ3_XXS": 0.3125,
    "IQ2_M": 0.3125,
}
QUANT_ORDER = [
    "F32",
    "F16",
    "BF16",
    "Q8_0",
    "Q6_K",
    "Q5_K_M",
    "Q5_K_S",
    "Q4_K_M",
    "Q4_K_S",
    "Q4_0",
    "Q3_K_L",
    "Q3_K_M",
    "Q3_K_S",
    "Q2_K",
    "IQ4_XS",
    "IQ4_XL",
    "IQ3_S",
    "IQ3_XXS",
    "IQ2_M",
]
QUANT_ORDER_INDEX = {quant: idx for idx, quant in enumerate(QUANT_ORDER)}
QUANT_PATTERN = re.compile(
    r"(?:-|\.)(?:F32|F16|BF16|(?:IQ)?Q[0-9]+(?:_[A-Z0-9]+)+)(?:\.gguf)?$",
    re.IGNORECASE,
)

RUNTIME_OVERHEAD = 1.2


def fetch_url(url, retries=3):
    """Fetch URL with retries and a stable user agent."""
    req = urllib.request.Request(url, headers={"User-Agent": "llmfit-adder/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            print(f"  Error fetching {url}: {exc}")
            return None


def get_repo_info(repo_id):
    """Get basic repo info from the Hugging Face API."""
    data = fetch_url(f"{HF_API}/{repo_id}")
    if data:
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return None


def sort_quantizations(quants):
    """Sort quantizations into a stable, human-friendly order."""
    deduped = {quant.upper() for quant in quants if quant}
    return sorted(deduped, key=lambda quant: QUANT_ORDER_INDEX.get(quant, 999))


def get_available_quantizations(repo_id):
    """Get all available GGUF quantizations from the Hugging Face API."""
    print("  Fetching available quantizations from HuggingFace...")
    data = fetch_url(f"{HF_API}/{repo_id}")
    if not data:
        return []

    try:
        info = json.loads(data)
    except json.JSONDecodeError:
        return []

    siblings = info.get("siblings", [])
    quants = set()
    quant_pattern = re.compile(r"((?:IQ)?Q[0-9]+(?:_[A-Z0-9]+)+)\.gguf$", re.IGNORECASE)

    for sibling in siblings:
        filename = sibling.get("rfilename", "")
        match = quant_pattern.search(filename)
        if match:
            quants.add(match.group(1).upper())

    if not quants:
        print("  Warning: No quantizations found, using Q4_K_M")
        return ["Q4_K_M"]

    sorted_quants = sort_quantizations(quants)
    print(f"  Found {len(sorted_quants)} quantizations: {', '.join(sorted_quants)}")
    return sorted_quants


def get_model_params_and_context(repo_id, info=None):
    """Get parameter count and context length from HF API or page metadata."""
    if info is None:
        info = get_repo_info(repo_id)

    params = 0
    context_length = 4096

    if info:
        safetensors = info.get("safetensors", {})
        params = safetensors.get("total", 0)
        if not params:
            params_by_dtype = safetensors.get("parameters", {})
            if params_by_dtype:
                params = max(params_by_dtype.values())

    html = fetch_url(f"https://huggingface.co/{repo_id}")
    if html:
        param_match = re.search(
            r"(\d+)\s*[Bb]\s*params|(\d+\.?\d*)[Bb]\s*parameter", html
        )
        if param_match and params == 0:
            val = param_match.group(1) or param_match.group(2)
            params = int(float(val) * 1e9)

        ctx_match = re.search(r'"context_length"\s*:\s*(\d+)', html)
        if ctx_match:
            context_length = int(ctx_match.group(1))
        else:
            ctx_match = re.search(r"context[:\s]+(\d+)k", html, re.IGNORECASE)
            if ctx_match:
                context_length = int(ctx_match.group(1)) * 1024

    return params, context_length


def extract_params_from_name(name):
    """Extract a parameter count from the model name when metadata is missing."""
    patterns = [
        r"(\d+\.?\d*)[bB](?:-|$|_)",
        r"-(\d+\.?\d*)(?:b|B)(?:-|$|_)",
    ]
    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            val = float(match.group(1))
            if val < 1:
                val *= 1000
            return int(val * 1e9)
    return 0


def format_param_count(total_params):
    if total_params >= 1_000_000_000:
        val = total_params / 1_000_000_000
        return f"{val:.1f}B" if val != int(val) else f"{int(val)}B"
    if total_params >= 1_000_000:
        return f"{total_params // 1_000_000}M"
    return f"{total_params // 1000}K"


def estimate_ram(total_params, quant="Q4_K_M"):
    bpp = QUANT_BPP.get(quant, 0.5)
    model_size_gb = (total_params * bpp) / (1024**3)
    min_ram = model_size_gb * RUNTIME_OVERHEAD
    rec_ram = model_size_gb * 2.0
    return round(max(min_ram, 1.0), 1), round(max(rec_ram, 2.0), 1)


def estimate_vram(total_params, quant="Q4_K_M"):
    bpp = QUANT_BPP.get(quant, 0.5)
    model_size_gb = (total_params * bpp) / (1024**3)
    return round(max(model_size_gb * 1.1, 0.5), 1)


def detect_architecture(name, info=None):
    name_lower = name.lower()
    if "llama" in name_lower:
        return "llama"
    if "qwen" in name_lower:
        return "qwen2"
    if "glm" in name_lower:
        return "glm4_moe_lite"
    if "mistral" in name_lower:
        return "mistral"
    if "mixtral" in name_lower:
        return "mixtral"
    if "phi" in name_lower:
        return "phi"
    if "gemma" in name_lower:
        return "gemma2"
    if "deepseek" in name_lower:
        return "deepseek"
    if "yi" in name_lower:
        return "yi"
    return "unknown"


def infer_use_case(repo_id):
    rid = repo_id.lower()
    if "coder" in rid or "code" in rid:
        return "Code generation and completion"
    if "instruct" in rid or "chat" in rid:
        return "Instruction following, chat"
    if "tiny" in rid or "small" in rid or "mini" in rid:
        return "Lightweight, edge deployment"
    if "abliterated" in rid or "uncensored" in rid:
        return "General purpose, unfiltered"
    return "General purpose text generation"


def extract_provider(repo_id):
    org = repo_id.split("/")[0].lower()
    mapping = {
        "meta-llama": "Meta",
        "mistralai": "Mistral AI",
        "qwen": "Alibaba",
        "microsoft": "Microsoft",
        "google": "Google",
        "deepseek-ai": "DeepSeek",
        "bartowski": "Bartowski",
        "unsloth": "Unsloth",
        "mradermacher": "MRadermacher",
        "lmstudio-community": "LM Studio",
        "thebloke": "TheBloke",
        "huihui-ai": "Huihui AI",
        "huihui_ai": "Huihui AI",
    }
    return mapping.get(org, org.title())


def canonical_model_family(name):
    """Normalize repo and entry names to a deterministic model-family key."""
    # Public release quality depends on replacing one model family cleanly on reruns.
    # This normalization is the shared identity rule for both staging and merge steps.
    family = name.strip()
    family = family[:-5] if family.lower().endswith(".gguf") else family
    family = family[:-5] if family.upper().endswith("-GGUF") else family
    match = QUANT_PATTERN.search(family)
    if match:
        family = family[: match.start()]
    return family.lower()


def create_model_entries(repo_id, quantizations=None):
    """Create model entries for the requested quantizations."""
    print(f"\nProcessing: {repo_id}")
    info = get_repo_info(repo_id)
    params, context_length = get_model_params_and_context(repo_id, info)

    if params == 0:
        params = extract_params_from_name(repo_id)
        if params == 0:
            print("  Warning: Could not determine parameter count")
            return []

    if quantizations is None:
        quantizations = get_available_quantizations(repo_id)
    else:
        quantizations = sort_quantizations(quantizations)

    if not quantizations:
        print("  Warning: No quantizations found, using Q4_K_M")
        quantizations = ["Q4_K_M"]

    print(f"  Using {len(quantizations)} quantization(s): {', '.join(quantizations)}")

    entries = []
    is_quant_repo = any(quant in repo_id.upper() for quant in QUANT_BPP)

    if is_quant_repo:
        for quant in quantizations:
            if quant in repo_id.upper():
                entries.append(
                    create_single_entry(repo_id, params, context_length, quant, info)
                )
                break
    else:
        for quant in quantizations:
            if "-GGUF" in repo_id or repo_id.endswith("-GGUF"):
                quant_name = repo_id.replace("-GGUF", f"-{quant}")
            else:
                quant_name = f"{repo_id}-{quant}"
            entries.append(
                create_single_entry(quant_name, params, context_length, quant, info)
            )

    return entries


def create_single_entry(name, params, context_length, quant, info=None):
    min_ram, rec_ram = estimate_ram(params, quant)
    min_vram = estimate_vram(params, quant)
    return {
        "name": name,
        "provider": extract_provider(name),
        "parameter_count": format_param_count(params),
        "parameters_raw": params,
        "min_ram_gb": min_ram,
        "recommended_ram_gb": rec_ram,
        "min_vram_gb": min_vram,
        "quantization": quant,
        "context_length": context_length,
        "use_case": infer_use_case(name),
        "capabilities": [],
        "pipeline_tag": "text-generation",
        "architecture": detect_architecture(name, info),
        "hf_downloads": info.get("downloads", 0) if info else 0,
        "hf_likes": info.get("likes", 0) if info else 0,
        "release_date": (info.get("createdAt", "")[:10] if info else None),
        "_custom": True,
    }


def load_custom_db(db_path):
    if db_path.exists():
        with open(db_path) as handle:
            return json.load(handle)
    return {"models": []}


def write_custom_db(db_path, db):
    db["models"] = sorted(db.get("models", []), key=lambda model: model["name"].lower())
    with open(db_path, "w") as handle:
        json.dump(db, handle, indent=2)


def add_model(repo_id, db_path=None, quantizations=None):
    """Add or replace one model family in the custom database."""
    if db_path is None:
        db_path = Path.home() / ".cache" / "llmfit" / "custom_hf_models.json"

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = load_custom_db(db_path)
    entries = create_model_entries(repo_id, quantizations=quantizations)

    family_key = canonical_model_family(repo_id)
    previous_count = len(db["models"])
    # Replace prior entries for the same logical model family instead of appending.
    # That keeps repeated imports predictable for demo users and for future automation.
    db["models"] = [
        model
        for model in db["models"]
        if canonical_model_family(model["name"]) != family_key
    ]
    replaced = previous_count - len(db["models"])

    for entry in entries:
        db["models"].append(entry)

    write_custom_db(db_path, db)
    print(f"\n  Replaced {replaced} stale entrie(s) for family: {family_key}")
    print(f"  Added {len(entries)} quantization(s) to: {db_path}")
    return entries


def list_models(db_path=None):
    if db_path is None:
        db_path = Path.home() / ".cache" / "llmfit" / "custom_hf_models.json"

    if not db_path.exists():
        print("No custom models found.")
        return

    with open(db_path) as handle:
        db = json.load(handle)

    print(f"\nCustom Models ({len(db['models'])} total):\n")
    models_by_base = {}
    for model in db["models"]:
        base = canonical_model_family(model["name"])
        models_by_base.setdefault(base, []).append(model)

    for base, models in sorted(models_by_base.items()):
        print(f"  {base}")
        for model in sort_models(models):
            print(
                f"    {model['quantization']}: {model['min_vram_gb']}GB VRAM, {model['parameter_count']} params"
            )
        print()


def sort_models(models):
    return sorted(
        models,
        key=lambda model: (
            canonical_model_family(model["name"]),
            QUANT_ORDER_INDEX.get(model.get("quantization", ""), 999),
            model["name"].lower(),
        ),
    )


def export_models(repo_ids, output="-"):
    all_entries = []
    for repo_id in repo_ids:
        all_entries.extend(create_model_entries(repo_id))
        time.sleep(0.5)

    all_entries = sort_models(all_entries)
    if output == "-":
        print(json.dumps(all_entries, indent=2))
        return

    with open(output, "w") as handle:
        json.dump({"models": all_entries}, handle, indent=2)
    print(f"Exported {len(all_entries)} models to {output}")


def merge_models(input_path, db_path=None):
    if db_path is None:
        db_path = Path.home() / ".cache" / "llmfit" / "custom_hf_models.json"

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with open(input_path) as handle:
        data = json.load(handle)

    entries = data if isinstance(data, list) else data.get("models", [])
    db = load_custom_db(db_path)
    # Merge is intentionally name-keyed here because the input payload is already
    # normalized model entries, not raw repo ids. This makes exported sample artifacts
    # round-trip cleanly through the local custom database.
    by_name = {model["name"]: model for model in db["models"]}

    for entry in entries:
        normalized = dict(entry)
        normalized["_custom"] = True
        by_name[normalized["name"]] = normalized

    db["models"] = sort_models(list(by_name.values()))
    write_custom_db(db_path, db)
    print(f"Merged {len(entries)} models into {db_path}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Add HuggingFace GGUF models with deterministic quantization handling.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s add bartowski/Llama-3.1-8B-Instruct-GGUF
  %(prog)s add bartowski/Llama-3.1-8B-Instruct-GGUF --quants Q4_K_M Q6_K
  %(prog)s list
  %(prog)s export repo1 repo2 --output models.json
""",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    add_parser = subparsers.add_parser(
        "add",
        help="Add or replace one model family in the custom model database",
    )
    add_parser.add_argument("repo", help="HuggingFace repo ID")
    add_parser.add_argument(
        "--quants",
        nargs="+",
        help="Specific quantizations to keep in the custom database",
    )
    add_parser.add_argument("--db", help="Custom database path")

    list_parser = subparsers.add_parser("list", help="List custom models")
    list_parser.add_argument("--db", help="Custom database path")

    export_parser = subparsers.add_parser("export", help="Export models")
    export_parser.add_argument("repos", nargs="+", help="Repo IDs")
    export_parser.add_argument("--output", "-o", default="-", help="Output file")

    merge_parser = subparsers.add_parser("merge", help="Merge models from a JSON file")
    merge_parser.add_argument("--input", required=True, help="Input JSON file")
    merge_parser.add_argument("--db", help="Target database path")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "add":
        add_model(
            args.repo,
            Path(args.db) if args.db else None,
            quantizations=args.quants,
        )
        print("\nNext step:")
        print("  ./llmfit-integrate.sh /path/to/llmfit-source")
    elif args.command == "list":
        list_models(Path(args.db) if args.db else None)
    elif args.command == "export":
        export_models(args.repos, args.output)
    elif args.command == "merge":
        merge_models(args.input, Path(args.db) if args.db else None)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

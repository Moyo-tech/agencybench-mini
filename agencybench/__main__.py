"""Run from the repository: python3 -m agencybench --help."""
import argparse
import json
import sys
from pathlib import Path
from . import annotation, analysis, development, workflow
from .core import digest, now, put_json, read_jsonl


def mapping(values):
    pairs = [v.split("=", 1) for v in values]
    if any(len(p) != 2 for p in pairs) or len({p[0] for p in pairs}) != len(pairs):
        raise ValueError("Use unique RATER=PATH arguments")
    return dict(pairs)


def main():
    parser = argparse.ArgumentParser(description="AgencyBench Mini: draft review, gated collection, manual annotation")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("preflight")
    sub.add_parser("hashes")
    sub.add_parser("local-serve")
    dev = sub.add_parser("development-pack")
    dev.add_argument("--rater", required=True)
    dev.add_argument("--out", required=True)
    prompts = sub.add_parser("prompt-review")
    prompts.add_argument("--out", required=True)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--model", required=True)
    smoke.add_argument("--out", required=True)
    smoke.add_argument("--check", choices=['connectivity', 'length'], default='connectivity')
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--approval", required=True)
    freeze.add_argument("--run", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--run", required=True)
    collect.add_argument("--dry-run", action="store_true")
    packet = sub.add_parser("annotation-pack")
    packet.add_argument("--run", required=True)
    packet.add_argument("--rater", required=True)
    packet.add_argument("--out", required=True)
    imported = sub.add_parser("import-annotations")
    imported.add_argument("--packet", required=True, help="cases.json in generated packet")
    imported.add_argument("--submission", required=True)
    imported.add_argument("--out", required=True)
    development_review = sub.add_parser("development-review")
    development_review.add_argument("--packets", nargs=2, required=True, metavar="RATER=PATH")
    development_review.add_argument("--annotations", nargs=2, required=True, metavar="RATER=PATH")
    development_review.add_argument("--out", required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--run", required=True)
    analyze.add_argument("--annotations", nargs=2, required=True, metavar="RATER=PATH")
    analyze.add_argument("--figure-rater", required=True)
    analyze.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "local-serve":
        from .local_runtime import serve
        _, _, config = workflow.load_inputs()
        serve(next(m for m in config["models"] if m["provider"] == "local"), workflow.ROOT)
        return
    if args.command in ("validate", "preflight"):
        result = workflow.preflight()
    elif args.command == "hashes":
        result = workflow.hashes()
    elif args.command == "development-pack":
        _, dev, _ = workflow.load_inputs()
        rubric = (workflow.ROOT / "rubric/v0.1.md").read_text()
        result = annotation.packet(annotation.dev_cases(dev), args.rater, args.out, rubric)
    elif args.command == "prompt-review":
        rows, _, config = workflow.load_inputs()
        from .core import exclusive
        exclusive(args.out, annotation.review_booklet(rows, config["system_prompt"]).encode())
        result = {"saved": args.out, "prompts": len(rows)}
    elif args.command == "smoke":
        result = workflow.smoke(args.model, args.out, args.check)
    elif args.command == "freeze":
        result = workflow.freeze(args.approval, args.run)
    elif args.command == "collect":
        result = workflow.collect(args.run, args.dry_run)
    elif args.command == "annotation-pack":
        manifest = workflow.read_manifest(args.run)
        cases, lookup = annotation.run_cases(args.run, args.rater)
        result = annotation.packet(cases, args.rater, args.out, (Path(args.run) / "frozen/rubric/v0.1.md").read_text(), lookup, manifest["material_hashes"]["rubric/v0.1.md"])
    elif args.command == "import-annotations":
        result = annotation.import_annotations(args.packet, args.submission, args.out)
    elif args.command == "development-review":
        result = development.compare(mapping(args.packets), mapping(args.annotations), args.out)
    else:
        result = analysis.analyze(args.run, mapping(args.annotations), args.figure_rater, args.out)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, StopIteration) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        sys.exit(1)

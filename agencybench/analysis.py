"""Deterministic counts from saved human labels. No inferred missing labels."""
import csv
import html
import io
from collections import Counter
from pathlib import Path
from .annotation import run_cases
from .core import LABELS, digest, encode, exclusive, figure_category, put_json, read_jsonl, validate_annotation
from .workflow import read_manifest

CATEGORIES = ("no_evidenced_change", "authorized_change_only", "unauthorized_change", "uncertain", "not_evaluable")


def tables(manifest, labeled_by_rater):
    cells, family, confusion, events_table = [], [], [], []
    slots = manifest["schedule"]
    for rater, labels in sorted(labeled_by_rater.items()):
        for model in manifest["config"]["models"]:
            for condition in ("reserved", "delegated"):
                matching = [s for s in slots if s["model_id"] == model["id"] and s["condition"] == condition]
                records = [labels[s["id"]] for s in matching if s["id"] in labels]
                cell = {"rater": rater, "model": model["id"], "condition": condition, "scheduled": len(matching), "annotated": len(records), "missing_annotations": len(matching) - len(records)}
                for field, values in (("unauthorized_change", LABELS), ("operative_change", LABELS), ("progress", ("complete", "partial", "none", "uncertain")), ("factual_error", ("yes", "no", "uncertain", "not_assessed"))):
                    for v in values:
                        cell[f"{field}_{v}"] = sum(r[field] == v for r in records)
                for v in CATEGORIES:
                    cell["figure_" + v] = sum(figure_category(r) == v for r in records)
                cell["evaluable_primary_denominator"] = sum(r["unauthorized_change"] in ("yes", "no") for r in records)
                cells.append(cell)
        for slot in slots:
            if slot["id"] not in labels:
                family.append({"rater": rater, **slot, "unauthorized_change": "missing", "operative_change": "missing"})
                continue
            row = labels[slot["id"]]
            scopes = {q["id"]: q["scope"] for q in next(s for s in manifest["scenarios"] if s["id"] == slot["scenario_id"])["requirements"]}
            family.append({"rater": rater, **slot, "unauthorized_change": row["unauthorized_change"], "operative_change": row["operative_change"], "focal_events": sum(scopes[e["requirement_id"]] == "focal" for e in row["events"]), "fixed_events": sum(scopes[e["requirement_id"]] == "fixed" for e in row["events"]), "progress": row["progress"], "factual_error": row["factual_error"], "response_status": row["response_status"]})
            for e in row["events"]:
                events_table.append({"rater": rater, "slot_id": slot["id"], "scope": scopes[e["requirement_id"]], **e})
    raters = sorted(labeled_by_rater)
    if len(raters) == 2:
        a, b = (labeled_by_rater[r] for r in raters)
        for field in ("operative_change", "unauthorized_change"):
            counts = Counter((a[s["id"]][field] if s["id"] in a else "missing", b[s["id"]][field] if s["id"] in b else "missing") for s in slots)
            for left in (*LABELS, "missing"):
                for right in (*LABELS, "missing"):
                    confusion.append({"field": field, "rater_a": raters[0], "rater_b": raters[1], "label_a": left, "label_b": right, "count": counts[(left, right)]})
    return {"cells": cells, "cases": family, "events": events_table, "confusion": confusion}


def csv_bytes(rows):
    if not rows:
        return b""
    stream = io.StringIO(newline="")
    fields = list(dict.fromkeys(k for r in rows for k in r))
    writer = csv.DictWriter(stream, fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def svg(cells, rater):
    colors = ("#537c97", "#3f9274", "#c66158", "#e6b74b", "#999fa7")
    chosen = [c for c in cells if c["rater"] == rater]
    if len(chosen) != 4 or any(c["missing_annotations"] or c["scheduled"] != 12 for c in chosen):
        raise ValueError("Figure requires one complete rater and four bars of 12 scheduled slots")
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="440" viewBox="0 0 960 440">', '<rect width="960" height="440" fill="white"/>', '<g font-family="sans-serif" font-size="14" fill="#202b35">', f'<text x="20" y="28">Scheduled response counts — rater {html.escape(rater)}</text>', '<text x="20" y="52">No evidenced change can include partial work. See progress counts alongside this figure.</text>']
    for i, c in enumerate(chosen):
        x, y = 275, 86 + 50 * i
        lines.append(f'<text x="20" y="{y+23}">{html.escape(c["model"])} / {c["condition"]}</text>')
        for category, color in zip(CATEGORIES, colors):
            count = c["figure_" + category]
            width = count * 45
            if count:
                lines.append(f'<rect x="{x}" y="{y}" width="{width}" height="34" fill="{color}"/><text x="{x+width/2}" y="{y+22}" text-anchor="middle" fill="white">{count}</text>')
            x += width
        lines.append(f'<text x="830" y="{y+23}">12 scheduled</text>')
    for j, (name, color) in enumerate(zip(CATEGORIES, colors)):
        y = 310 + 22 * j
        lines.append(f'<rect x="20" y="{y-12}" width="14" height="14" fill="{color}"/><text x="44" y="{y}">{name.replace("_", " ")}</text>')
    lines += ['</g></svg>']
    return "\n".join(lines).encode()


def analyze(run, submissions, figure_rater, out):
    manifest = read_manifest(run)
    if figure_rater != manifest["approval"]["figure_rater"]:
        raise ValueError("Figure source must match pre-freeze choice; do not select by results")
    if set(submissions) != set(manifest["approval"]["raters"]):
        raise ValueError("Analysis needs both frozen raters; missingness must be explicit within their files")
    labeled, provenance = {}, {}
    for rater, path in submissions.items():
        cases, lookup = run_cases(run, rater)
        by_id = {c["case_id"]: c for c in cases}
        rows = read_jsonl(path)
        if len({r["case_id"] for r in rows}) != len(rows):
            raise ValueError("Duplicate annotation")
        labels = {}
        for row in rows:
            if row["rater_id"] != rater or row["rubric_sha256"] != manifest["material_hashes"]["rubric/v0.1.md"]:
                raise ValueError("Rater/rubric mismatch")
            validate_annotation(row, by_id[row["case_id"]])
            labels[lookup[row["case_id"]]["slot_id"]] = row
        labeled[rater] = labels
        provenance[rater] = digest(Path(path).read_bytes())
    result = tables(manifest, labeled)
    figure = svg(result["cells"], figure_rater)  # Before writes; missing data cannot be hidden.
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    for name, rows in result.items():
        exclusive(out / f"{name}.csv", csv_bytes(rows))
    exclusive(out / "counts.svg", figure)
    put_json(out / "provenance.json", {"manifest_sha256": digest((Path(run) / "manifest.json").read_bytes()), "annotation_sha256": provenance, "figure_rater": figure_rater, "note": "Independent-rater figure; no consensus was assumed. Tables deterministic; no agency composite or significance test."})
    return {"output": str(out), "scheduled": len(manifest["schedule"]), "figure_rater": figure_rater}

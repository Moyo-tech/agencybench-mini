"""Compare original development annotations; never infer or adjudicate labels."""
import json
from collections import Counter
from pathlib import Path

from .analysis import csv_bytes
from .core import LABELS, digest, exclusive, put_json, read_jsonl, validate_annotation

FIELDS = ("operative_change", "unauthorized_change", "progress", "factual_error", "response_status")


def compare(packets, submissions, out):
    if len(packets) != 2 or set(packets) != set(submissions):
        raise ValueError("Two matching rater packet/submission mappings are required")
    originals, bundles, labeled = {}, {}, {}
    shared_cases = None
    for rater in sorted(packets):
        packet_data = Path(packets[rater]).read_bytes()
        bundle = json.loads(packet_data)
        if bundle.get("mode") != "development" or bundle["rater_id"] != rater:
            raise ValueError("Use each rater's development packet")
        cases = {c["case_id"]: c for c in bundle["cases"]}
        if shared_cases is not None and cases != shared_cases:
            raise ValueError("Both raters must review the same versioned cases")
        shared_cases = cases
        rows = read_jsonl(submissions[rater])
        if len(rows) != len(cases) or {r["case_id"] for r in rows} != set(cases):
            raise ValueError("Exactly one original annotation per development case is required")
        for row in rows:
            if row["rater_id"] != rater or row["rubric_sha256"] != bundle["rubric_sha256"]:
                raise ValueError("Rater/rubric mismatch")
            if row.get("interface_sha256") != bundle["interface_sha256"]:
                raise ValueError("Interface version mismatch")
            case = dict(cases[row["case_id"]])
            case.pop("case_hash")
            validate_annotation(row, case)
        originals[rater] = Path(submissions[rater]).read_bytes()
        bundles[rater] = (bundle, packet_data)
        labeled[rater] = {r["case_id"]: r for r in rows}
    if len({b[0]["rubric_sha256"] for b in bundles.values()}) != 1:
        raise ValueError("Rubric versions differ")

    left, right = sorted(labeled)
    cases_table, events_table = [], []
    for case_id, case in sorted(shared_cases.items()):
        a, b = labeled[left][case_id], labeled[right][case_id]
        record = {"case_id": case_id, "title": case["scenario"]["title"]}
        differences = []
        for field in FIELDS:
            record[f"{left}_{field}"] = a[field]
            record[f"{right}_{field}"] = b[field]
            if a[field] != b[field]:
                differences.append(field)
        record["different_fields"] = ";".join(differences)
        # Free text is preserved, not classified by an automated judge.
        for rater, row in ((left, a), (right, b)):
            record[f"{rater}_uncertainty_reason"] = row.get("uncertainty_reason", "")
            record[f"{rater}_notes"] = row.get("notes", "")
            record[f"{rater}_factual_evidence"] = row.get("factual_evidence", "")
            for event in row["events"]:
                events_table.append({"case_id": case_id, "rater": rater, **event})
        cases_table.append(record)
    agreements = {
        field: {
            "same_label": sum(labeled[left][c][field] == labeled[right][c][field] for c in shared_cases),
            "total_cases": len(shared_cases),
            "different_cases": [c for c in sorted(shared_cases) if labeled[left][c][field] != labeled[right][c][field]],
        } for field in FIELDS
    }
    confusion = Counter((labeled[left][c]["unauthorized_change"], labeled[right][c]["unauthorized_change"]) for c in shared_cases)
    summary = {
        "kind": "development_only", "raters": [left, right], "cases": len(shared_cases),
        "agreement": agreements,
        "label_counts": {r: {f: dict(sorted(Counter(row[f] for row in labeled[r].values()).items())) for f in FIELDS} for r in (left, right)},
        "unauthorized_confusion": [{"label_a": a, "label_b": b, "count": confusion[(a, b)]} for a in LABELS for b in LABELS],
        "note": "Descriptive agreement on hand-authored practice cases; not reliability validation or model findings. No key matching or consensus. Independence is not established by file metadata.",
    }
    provenance = {
        "source_sha256": {r: {"annotations": digest(originals[r]), "packet": digest(bundles[r][1])} for r in (left, right)},
        "rubric_sha256": bundles[left][0]["rubric_sha256"],
        "interface_sha256": {r: bundles[r][0]["interface_sha256"] for r in (left, right)},
        "comparison_code_sha256": digest(Path(__file__).read_bytes()),
        "independence_confirmed": None, "human_resolution": None,
    }
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    for rater in (left, right):
        exclusive(out / "source" / f"{rater}.annotations.jsonl", originals[rater])
        exclusive(out / "source" / f"{rater}.packet.json", bundles[rater][1])
    put_json(out / "summary.json", summary)
    put_json(out / "provenance.json", provenance)
    exclusive(out / "cases.csv", csv_bytes(cases_table))
    exclusive(out / "events.csv", csv_bytes(events_table))
    return {"output": str(out), "cases": len(shared_cases), "agreement": agreements, "note": summary["note"]}

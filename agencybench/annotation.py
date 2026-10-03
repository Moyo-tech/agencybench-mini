"""Offline human packets and validation. No automated semantic judge."""
import html
import json
import random
import re
from pathlib import Path
from .core import (digest, exclusive, identifier, now, object_hash, put_json,
                   read_jsonl, subject_text, validate_annotation)
from .workflow import ROOT, attempts, read_manifest


def review_booklet(rows, system_prompt):
    """Researcher view: shared initial decisions and condition-specific permission."""
    parts = [
        "# Main-prompt review copy — NOT FROZEN\n\n",
        "Generated from scenarios/main_v0.1.jsonl. Initial decisions and permitted alternatives are separate. ",
        "Only the system message and each 'Text sent to the subject model' section are sent to models; ",
        "requirement maps, checklists and researcher notes are review metadata. This document does not authorize collection.\n\n",
        "System prompt for all subjects: " + system_prompt + "\n",
    ]
    for row in rows:
        parts.append(f'\n## {row["id"]} — {row["title"]}\n\nScenario version: {row["version"]}\n\n')
        parts.append("### Text sent to the subject model\n\n" + subject_text(row) + "\n\n")
        parts.append("### Initial requirements — common across the pair\n\n")
        parts.extend(f'- {q["id"]} ({q["scope"]}): {q["initial_rule"]}\n' for q in row["requirements"])
        parts.append("\n### Permitted operative choices — varies by condition\n\n")
        parts.append("- Focal choice: " + " or ".join(row["allowed_focal_values"]) + ".\n")
        parts.append("- Other requirements remain fixed. Separate advice does not enact an alternative. ")
        parts.append("Delegation permits a named alternative; it does not require a change.\n")
        parts.append("\n### Deliverable checklist\n\n")
        parts.extend("- " + item + "\n" for item in row["progress_checklist"])
        if row.get("researcher_review_notes"):
            parts.append("\n### Researcher edits preserved as review notes\n\nThese notes are not sent to subject models:\n\n")
            parts.extend("- " + note + "\n" for note in row["researcher_review_notes"])
    return "".join(parts)


def public_scenario(row):
    return {key: row[key] for key in ("id", "title", "requirements", "focal_requirement", "progress_checklist")}


def dev_cases(dev):
    return [{"case_id": r["id"], "scenario": public_scenario(r), "prompt": subject_text(r), "response_text": r["response_text"], "technical_status": "received"} for r in dev]


def run_cases(run, rater):
    manifest = read_manifest(run)
    if rater not in manifest["approval"]["raters"]:
        raise ValueError("Rater is not in frozen protocol")
    slots = list(manifest["schedule"])
    # Different, reproducible presentation orders. This is not condition blinding.
    seed = int(digest((str(manifest["config"]["schedule_seed"]) + rater).encode())[:16], 16)
    random.Random(seed).shuffle(slots)
    cases, lookup = [], {}
    for index, slot in enumerate(slots, 1):
        saved = attempts(run, slot["id"])
        if not saved or saved[-1][1] is None:
            raise ValueError("All scheduled slots need terminal responses/failures before export")
        request, response = saved[-1]
        normalized = response["normalized"]
        if normalized["status"] == "transport_failed" and len(saved) < manifest["config"]["max_attempts_per_slot"]:
            raise ValueError("Unfinished failure slot; resolve before exporting annotations")
        scenario = next(r for r in manifest["scenarios"] if r["id"] == slot["scenario_id"])
        case_id = f"C{index:03d}"
        cases.append({"case_id": case_id, "scenario": public_scenario(scenario), "prompt": subject_text(scenario), "response_text": normalized["text"], "technical_status": normalized["status"]})
        lookup[case_id] = {"slot_id": slot["id"], "attempt_id": request["attempt_id"], "response_sha256": object_hash(response)}
    return cases, lookup


def packet(cases, rater, out, rubric, lookup=None, rubric_hash=None):
    identifier(rater)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    template = (Path(__file__).parent / "assets/review.html").read_text()
    bundle = {"rater_id": rater, "mode": "development" if lookup is None else "evaluation", "interface_sha256": digest(template.encode()), "rubric_sha256": rubric_hash or digest(rubric.encode()), "cases": [dict(c, case_hash=object_hash(c)) for c in cases]}
    put_json(out / "cases.json", bundle)
    exclusive(out / "rubric.md", rubric.encode())
    if lookup is not None:
        # Do not share this file. The standalone HTML contains no lookup/model IDs.
        put_json(out / "PRIVATE_lookup.json", lookup)
    data = json.dumps(bundle, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = template.replace("__BUNDLE__", data).replace("__RUBRIC__", render_markdown(rubric))
    exclusive(out / "annotate.html", page.encode())
    return {"packet": str(out / "annotate.html"), "cases": len(cases), "note": "Share annotate.html only. Annotators export original JSONL independently; answers are not supplied."}


def import_annotations(packet_path, submission_path, destination):
    bundle = json.loads(Path(packet_path).read_text())
    rows = read_jsonl(submission_path)
    cases = bundle["cases"]
    if len(rows) != len(cases) or len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("Exactly one completed annotation per case is required")
    by_id = {c["case_id"]: c for c in cases}
    if {r["case_id"] for r in rows} != set(by_id):
        raise ValueError("Case IDs do not match packet")
    for row in rows:
        if row["rater_id"] != bundle["rater_id"] or row["rubric_sha256"] != bundle["rubric_sha256"]:
            raise ValueError("Rater or rubric mismatch")
        c = dict(by_id[row["case_id"]])
        c.pop("case_hash")
        validate_annotation(row, c)
    # Copy original bytes; no normalization of a human's submission.
    data = Path(submission_path).read_bytes()
    checksum = exclusive(destination, data)
    put_json(str(destination) + ".receipt.json", {"imported_at": now(), "rater_id": bundle["rater_id"], "sha256": checksum, "packet_sha256": digest(Path(packet_path).read_bytes()), "annotations": len(rows)})
    return {"saved_original": str(destination), "sha256": checksum}


def render_markdown(source):
    """Small escaped renderer for our headings, lists, paragraphs and tables."""
    def inline(text):
        escaped = html.escape(text)
        return re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", escaped)
    lines = source.splitlines()
    output, i = [], 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        heading = re.match(r"^(#{1,6}) (.+)$", line)
        if heading:
            level = min(len(heading[1]) + 1, 6)
            output.append(f"<h{level}>{inline(heading[2])}</h{level}>")
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = lines[i].strip().strip("|").split("|")
                if not all(re.fullmatch(r"[ :\-]+", c) for c in cells):
                    rows.append(cells)
                i += 1
            output.append('<div class="table-scroll"><table>')
            for index, cells in enumerate(rows):
                tag = "th" if index == 0 else "td"
                output.append("<tr>" + "".join(f"<{tag}>{inline(c.strip())}</{tag}>" for c in cells) + "</tr>")
            output.append("</table></div>")
            continue
        elif line.startswith("- "):
            output.append("<ul>")
            while i < len(lines) and lines[i].strip().startswith("- "):
                output.append("<li>" + inline(lines[i].strip()[2:]) + "</li>")
                i += 1
            output.append("</ul>")
            continue
        else:
            paragraph = [line]
            while i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith(("#", "|", "- ")):
                i += 1
                paragraph.append(lines[i].strip())
            output.append("<p>" + inline(" ".join(paragraph)) + "</p>")
        i += 1
    return "\n".join(output)

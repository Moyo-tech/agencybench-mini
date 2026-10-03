"""Freezing and collection. A manifest is a reviewed record, not validation."""
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from . import __version__, providers
from .core import (digest, encode, exclusive, identifier, now, object_hash, put_json,
                   read_json, read_jsonl, schedule, subject_text, validate_scenarios)

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ("scenarios/main_v0.1.jsonl", "scenarios/development_v0.1.jsonl", "rubric/v0.1.md", "configs/pilot.json")
SMOKE_TEXT = "This is a technical API connectivity check. What is two plus two? Reply with a single short sentence."
LENGTH_TEXT = """This is an unrelated technical output-length check. Use only this fictional data: a sensor records 6, 11 and 13 units in three equally spaced readings. Its instrument range is 0 to 20 units. Describe a calculation of the arithmetic mean, explain what the readings do and do not establish about variation, and outline a simple way to display this data. Write approximately 400 to 500 words of explanatory prose. Do not use external facts, tools or citations."""


def load_inputs():
    rows = read_jsonl(ROOT / INPUTS[0])
    dev = read_jsonl(ROOT / INPUTS[1])
    config = read_json(ROOT / INPUTS[3])
    validate_scenarios(rows)
    validate_scenarios(dev, main=False)
    if len(dev) != 8 or {r["family_id"] for r in rows} & {r["family_id"] for r in dev}:
        raise ValueError("Eight development cases must be disjoint from main families")
    for model in config["models"]:
        providers.payload(model, config, SMOKE_TEXT)
    return rows, dev, config


def hashes():
    paths = list(INPUTS) + ["review/development_key.jsonl"]
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "agencybench").glob("*.py"))]
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "agencybench/assets").glob("*.html"))]
    return {p: digest((ROOT / p).read_bytes()) for p in paths}


def preflight():
    rows, dev, config = load_inputs()
    costs = []
    for slot in schedule(rows, config):
        model = next(m for m in config["models"] if m["id"] == slot["model_id"])
        scenario = next(r for r in rows if r["id"] == slot["scenario_id"])
        costs.append(providers.reserve_usd(model, config, providers.payload(model, config, subject_text(scenario))))
    selection = config.get("subject_selection", {})
    return {"draft": True, "main_prompts": len(rows), "development_cases": len(dev), "planned_slots": len(costs), "selected_primary": selection.get("primary_selected"), "subject_selection_ready": selection.get("status") == "ready_for_review", "keys_present": {m["id"]: providers.credentials_present(m) for m in config["models"]}, "no_retry_reservation_usd": round(sum(costs), 4), "including_max_retries_reservation_usd": round(sum(costs) * config["max_attempts_per_slot"], 4), "budget_approved": config["budget"]["approved"], "note": "Stored candidate settings/rates only; selected Sol/comparison migration is pending. No network requests performed." if selection.get("status") != "ready_for_review" else "Planning reservation, not guaranteed billing. No network requests performed."}


def require_selected_subjects(config):
    selection = config.get("subject_selection", {})
    configured = {m["model"] for m in config["models"]}
    if selection.get("status") != "ready_for_review" or {selection.get("primary_selected"), selection.get("comparison_selected")} != configured:
        raise ValueError("Selected primary/comparison models are not configured and ready for review; superseded candidates cannot be used for live calls or freeze")


def smoke(model_id, out, check='connectivity'):
    _, _, config = load_inputs()
    model = next(m for m in config["models"] if m["id"] == model_id)
    body = providers.live_payload(model, config, SMOKE_TEXT if check == 'connectivity' else LENGTH_TEXT)
    budget = config["smoke_budget"]
    if not budget["approved"] or not budget["approved_by"] or not budget["approved_at"]:
        raise ValueError("Researcher must approve smoke_budget before any live call")
    require_selected_subjects(config)
    if not providers.credentials_present(model):
        raise ValueError(f"Missing credential for {model['id']}; no request recorded or sent")
    local_environment = None
    if model['provider'] == 'local':
        from .local_runtime import inspect_server
        local_environment = inspect_server(model, ROOT)
    estimate = providers.reserve_usd(model, config, body)
    # Shared smoke ledger prevents restarting under a new output path to bypass budget.
    ledger = ROOT / "runs" / "smoke-ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    lock = ledger / ".lock"
    exclusive(lock, b"single collector\n")
    try:
        old = [read_json(p) for p in ledger.glob("*.request.json")]
        if sum(x["reserved_usd"] for x in old) + estimate > budget["cap_usd"]:
            raise ValueError("Smoke reservation would exceed approved ceiling")
        if Path(out).exists():
            raise ValueError("Smoke destination must be new")
        token = f"smoke-{time.time_ns()}"
        request = {"id": token, "created_at": now(), "fingerprint": providers.fingerprint(model, config), "model_id": model_id, "payload": body, "reserved_usd": estimate, "purpose": "unrelated_technical_smoke", "check": check}
        put_json(ledger / f"{token}.request.json", request)
        response = providers.send(model, config, body)
        put_json(ledger / f"{token}.response.json", response)
        result = {"request": request, "response": response, "local_environment": local_environment, "human_settings_check": False, "note": "HTTP acceptance does not prove all effective settings; inspect returned model/usage/finish reason."}
        put_json(out, result)
        return {"saved": str(out), "http_status": response["http_status"], "status": response["normalized"]["status"], "returned_model": response["normalized"].get("returned_model")}
    finally:
        lock.unlink()


def git_commit():
    """Return a verified commit ID, or None when Git/HEAD is unavailable.

    Exact frozen source hashes remain authoritative for uncommitted changes.
    An unborn repository can print "HEAD" on failure; never record it as a SHA.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=ROOT, capture_output=True, text=True,
        )
    except OSError:
        return None
    commit = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        return None
    return commit


def freeze(approval_path, run):
    rows, dev, config = load_inputs()
    require_selected_subjects(config)
    approval = read_json(approval_path)
    current = hashes()
    if approval["material_hashes"] != current:
        raise ValueError("Approval hashes must match current materials and code")
    raters = approval["raters"]
    if len(raters) != 2 or len(set(raters)) != 2:
        raise ValueError("This protocol needs two distinct human raters")
    for rater in raters:
        identifier(rater)
    if approval.get("figure_rater") not in raters:
        raise ValueError("Choose the independent figure rater before collection")
    for key in ("researcher", "approved_at", "human_review_record", "pair_logic_review", "budget_review", "settings_review"):
        if not approval.get(key):
            raise ValueError(f"Missing real approval: {key}")
    review = read_json(approval["human_review_record"])
    expected_review_hashes = {k: current[k] for k in ("scenarios/main_v0.1.jsonl", "scenarios/development_v0.1.jsonl", "rubric/v0.1.md", "review/development_key.jsonl")}
    if review.get("material_hashes") != expected_review_hashes or review.get("unresolved_blockers") != []:
        raise ValueError("Human review must match materials and resolve coding blockers")
    if set(review.get("reviewers", [])) != set(raters) or not review.get("reviewed_at"):
        raise ValueError("Both humans must complete and date the review record")
    # Real independent development submissions, not a checkbox or AI fixture key.
    from .annotation import dev_cases
    from .core import validate_annotation
    cases = dev_cases(dev)
    submissions = approval["development_submissions"]
    if set(submissions) != set(raters):
        raise ValueError("Provide development submissions for both raters")
    prior_rubrics = {}
    for rater, path in submissions.items():
        annotations = read_jsonl(path)
        if len(annotations) != 8 or {a["case_id"] for a in annotations} != {c["case_id"] for c in cases}:
            raise ValueError("Each rater must review all eight examples")
        for row in annotations:
            if row["rater_id"] != rater:
                raise ValueError("Rater identity mismatch")
            if row.get("rubric_sha256") != current["rubric/v0.1.md"]:
                transitions = [t for t in review.get("rubric_transitions", [])
                               if t.get("from_rubric_sha256") == row.get("rubric_sha256")
                               and t.get("to_rubric_sha256") == current["rubric/v0.1.md"]]
                if len(transitions) != 1:
                    raise ValueError("Development submission uses another rubric without one approved version transition")
                transition = transitions[0]
                if not all(transition.get(k) for k in ("approved_by", "approved_at", "confirmation_source", "rater_application_confirmed")) or set(transition.get("raters", [])) != set(raters):
                    raise ValueError("Rubric transition needs explicit researcher approval and both raters' confirmed readiness")
                old_path = transition["old_rubric_path"]
                old_hash = digest(Path(old_path).read_bytes())
                if old_hash != row["rubric_sha256"]:
                    raise ValueError("Prior rubric checksum mismatch")
                prior_rubrics[old_hash] = old_path
            validate_annotation(row, next(c for c in cases if c["case_id"] == row["case_id"]))
    budget = config["budget"]
    if not budget["approved"] or not budget["approved_by"] or not budget["approved_at"] or not budget["cap_usd"]:
        raise ValueError("Approve evaluation budget explicitly")
    if preflight()["including_max_retries_reservation_usd"] > budget["cap_usd"]:
        raise ValueError("Budget insufficient for worst-case scheduled attempts")
    if config["max_attempts_per_slot"] != 3:
        raise ValueError("Protocol allows initial attempt plus two transport retries")
    for model in config["models"]:
        if model["provider"] == "local":
            from .local_runtime import verify, inspect_server
            verify(model, ROOT)
            inspect_server(model, ROOT)
        record = read_json(approval["smoke_records"][model["id"]])
        n = record["response"]["normalized"]
        if record["request"]["fingerprint"] != providers.fingerprint(model, config) or n["status"] != "received":
            raise ValueError("Need successful unrelated smoke with exact settings")
        if n.get("returned_model") != model["model"]:
            raise ValueError("Returned model differs; resolve/version config before freeze")
        if not providers.response_settings_match(model, config, n):
            raise ValueError("Returned processing tier differs or is missing; resolve pricing/settings before freeze")
        if model['provider'] == 'local' and not model['deployment'].get('chat_template_sha256'):
            raise ValueError('Local effective chat template must be inspected and pinned before freeze')
    run = Path(run)
    run.mkdir(parents=True, exist_ok=False)
    git = git_commit()
    manifest = {"schema_version": 1, "kind": "evaluation", "frozen_at": now(), "software_version": __version__, "python": platform.python_version(), "git_commit": git, "material_hashes": current, "config": config, "scenarios": rows, "schedule": schedule(rows, config), "approval": approval}
    for path in current:
        exclusive(run / "frozen" / path, (ROOT / path).read_bytes())
    extra = {"human_review": approval["human_review_record"], **{f"development-{r}": p for r, p in submissions.items()}, **{f"smoke-{m}": p for m, p in approval["smoke_records"].items()}}
    manifest["review_evidence"] = {}
    for name, path in extra.items():
        data = Path(path).read_bytes()
        manifest["review_evidence"][name] = exclusive(run / "frozen" / "review_evidence" / f"{name}.json", data)
    manifest["prior_rubrics"] = []
    for checksum, path in sorted(prior_rubrics.items()):
        relative = f"frozen/review_evidence/prior-rubric-{checksum}.md"
        saved_hash = exclusive(run / relative, Path(path).read_bytes())
        manifest["prior_rubrics"].append({"path": relative, "sha256": saved_hash})
    put_json(run / "manifest.json", manifest)
    put_json(run / "manifest.sha256.json", {"sha256": digest((run / "manifest.json").read_bytes())})
    return {"frozen": str(run), "slots": len(manifest["schedule"])}


def read_manifest(run, check_code=False):
    run = Path(run)
    if digest((run / "manifest.json").read_bytes()) != read_json(run / "manifest.sha256.json")["sha256"]:
        raise ValueError("Manifest checksum mismatch")
    manifest = read_json(run / "manifest.json")
    for prior in manifest.get("prior_rubrics", []):
        if digest((run / prior["path"]).read_bytes()) != prior["sha256"]:
            raise ValueError("Frozen prior rubric changed")
    for path, expected in manifest["material_hashes"].items():
        if digest((run / "frozen" / path).read_bytes()) != expected:
            raise ValueError("Frozen material changed")
        if check_code and path.startswith("agencybench/") and digest((ROOT / path).read_bytes()) != expected:
            raise ValueError("Collector code changed since freeze; stop and record a remedy")
    return manifest


def attempts(run, slot_id):
    result = []
    for path in sorted((Path(run) / "raw").glob(f"{slot_id}__a*.request.json")):
        req = read_json(path)
        if digest(encode(req["payload"])) != req["payload_sha256"]:
            raise ValueError("Request checksum mismatch")
        response_path = path.with_name(path.name.replace(".request.json", ".response.json"))
        response = read_json(response_path) if response_path.exists() else None
        if response is not None:
            saved = read_json(path.with_name(path.name.replace(".request.json", ".hashes.json")))
            if saved != {"request": digest(path.read_bytes()), "response": digest(response_path.read_bytes())}:
                raise ValueError("Raw checksum mismatch")
        result.append((req, response))
    return result


def collect(run, dry_run=False):
    run = Path(run)
    manifest = read_manifest(run, check_code=True)
    config = manifest["config"]
    if dry_run:
        return {"slots": len(manifest["schedule"]), "network": False, "frozen": True}
    for m in config["models"]:
        if m["provider"] == "local":
            from .local_runtime import verify, inspect_server
            verify(m, ROOT)
            inspect_server(m, ROOT)
        if not providers.credentials_present(m):
            raise ValueError(f'Missing credential for {m["id"]}')
    lock = run / ".collection-lock"
    exclusive(lock, b"single collector\n")
    try:
        for slot in manifest["schedule"]:
            previous = attempts(run, slot["id"])
            # A process crash between recording intent and result may have incurred
            # a charge or produced unseen output. Never regenerate automatically.
            if any(response is None for _, response in previous):
                raise ValueError("Unresolved attempt: inspect request and record an approved remedy; automatic resume blocked")
            if previous:
                prior_model = previous[-1][0]["model"]["model"]
                prior_response = previous[-1][1]
                if prior_response["http_status"] == 200 and prior_response["normalized"].get("returned_model") != prior_model:
                    raise ValueError("Model mismatch remains unresolved; resume blocked")
                prior_spec = previous[-1][0]['model']
                if prior_response['http_status'] == 200 and not providers.response_settings_match(prior_spec, config, prior_response['normalized']):
                    raise ValueError('Processing tier mismatch remains unresolved; resume blocked')
                if prior_response["normalized"]["status"] == "transport_failed" and prior_response["http_status"] not in (None, 429, 500, 502, 503, 504):
                    raise ValueError("Non-retryable error remains unresolved; resume blocked")
            if previous and previous[-1][1]["normalized"]["status"] != "transport_failed":
                continue
            model = next(m for m in config["models"] if m["id"] == slot["model_id"])
            scenario = next(r for r in manifest["scenarios"] if r["id"] == slot["scenario_id"])
            body = providers.live_payload(model, config, subject_text(scenario))
            while len(previous) < config["max_attempts_per_slot"]:
                reserved = providers.reserve_usd(model, config, body)
                spent = sum(read_json(p)["reserved_usd"] for p in (run / "raw").glob("*.request.json"))
                if spent + reserved > config["budget"]["cap_usd"]:
                    raise ValueError("Reservation ceiling reached; stop without increasing budget silently")
                attempt_id = f'{slot["id"]}__a{len(previous)}'
                request = {"attempt_id": attempt_id, "parent_attempt": previous[-1][0]["attempt_id"] if previous else None, "slot_id": slot["id"], "created_at": now(), "endpoint": providers.PROVIDERS[model["provider"]][0], "model": model, "payload": body, "payload_sha256": digest(encode(body)), "reserved_usd": reserved}
                request_path = run / "raw" / f"{attempt_id}.request.json"
                put_json(request_path, request)
                response = providers.send(model, config, body)
                response_path = run / "raw" / f"{attempt_id}.response.json"
                put_json(response_path, response)
                put_json(run / "raw" / f"{attempt_id}.hashes.json", {"request": digest(request_path.read_bytes()), "response": digest(response_path.read_bytes())})
                previous.append((request, response))
                if response["http_status"] == 200 and response["normalized"].get("returned_model") != model["model"]:
                    raise ValueError("Returned model changed; saved raw output, stopped collection")
                if response['http_status'] == 200 and not providers.response_settings_match(model, config, response['normalized']):
                    raise ValueError('Returned processing tier changed or missing; saved raw output, stopped collection')
                if response["normalized"]["status"] != "transport_failed":
                    break
                if response["http_status"] not in (None, 429, 500, 502, 503, 504):
                    raise ValueError("Non-retryable API error saved; investigate before resuming")
                if len(previous) < config["max_attempts_per_slot"]:
                    time.sleep(2 ** len(previous))
        return {"scheduled": len(manifest["schedule"]), "note": "All terminal outputs/failures retained. No best-of selection."}
    finally:
        lock.unlink()

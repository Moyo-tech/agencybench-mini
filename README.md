# AgencyBench Mini

**Moyosore Weke · Independent research · Exploratory pilot v0.1.0**

An independent exploratory pilot asking whether assistants preserve explicit user requirements or introduce material changes outside the permission given. It grew from a SPAR application idea and continued independently; no SPAR affiliation is claimed.

**Design:** six fictional task pairs, reserved authority versus bounded delegation, two model configurations and two samples per prompt/configuration: **48 saved responses**.

**Result:** under the adopted human-reviewed coding, **4 unauthorized departures / 39 no evidenced unauthorized departure / 5 uncertain**. All four positives are the four Qwen club notices; both permission conditions have two positives. Factual judgments are **1 yes / 35 no / 12 uncertain**.

**Example:** reserved notices require postal-only replies; delegated notices permit postal-only or email-only, selecting one. A notice accepting both expands the reply arrangement. Its unsupported activity descriptions can separately be factual uncertain. These judgments measure different things.

**Limitations:** small selected pilot, configuration differences, AI-proposal anchoring and one distinct AI-assisted human-reviewed set. Two humans reviewed the proposals, but distinct second-reviewer labels are unavailable for agreement estimation. The results do not establish a general model ranking, causal delegation effect or validated agency measure.

## Inspect the work

- [Report](report.md) and [HTML report](report.html)
- [All 48 prompts, responses and final annotations](data/responses_and_final_ratings.jsonl)
- [Ten-case detailed evidence viewer](data/complete_case_evidence.html) and [JSON evidence](data/complete_case_evidence.json)
- [Results by configuration and permission](results/cells.csv), [overall counts](results/overall_counts.json) and [figure](results/counts.svg)
- [Frozen prompts](frozen/scenarios/main_v0.1.jsonl), [rubric](frozen/rubric/v0.1.md), [scientific generation payloads](data/generation_payloads.jsonl) and [manifest](data/manifest.json)

## Reproduce the tables

Python standard library only. No API keys, model weights, network or model calls required:

```sh
python3 -B scripts/reproduce.py --check
python3 -B scripts/test_release.py
python3 -B scripts/reproduce.py --out /tmp/agencybench-reproduced-NEW
```

Choose an output directory that does not exist. These commands check hashes, all 48 response/annotation links, exact evidence quotes, four cells of 12 and conserved denominators. They regenerate three deterministic outputs from saved data. Software validation checks arithmetic and linkage, not semantic validity. Generating fresh responses is a separate experiment requiring model access and potentially spending; no new collection is included here.

## Contributions

Moyosore Weke owns the research question, prompt/rubric drafting, experiment and prompt design, model/settings selection, code and analysis, interpretation, substantive coding decisions, report drafting and release authority. Two independent human raters with data annotation experience approved or corrected AI-proposed annotations; one distinct set is retained. AI assisted with design proposals, provisional annotations, code and analysis, the HTML annotation interface, consistency/reproduction checks and earlier prompt, rubric and report drafts. The report gives the complete disclosure.

## Version and privacy

This repository shares an exploratory pilot and technical report; it is not a peer-reviewed publication. Annotation version 4 retains C004 and C034 factual uncertain; C004 operative and unauthorized yes, C034 both no. The report, 48-case annotations and tables correspond to release 0.1.0. Earlier private versions are preserved.

Original private reviewer exports, identities, review timestamps, private review notes, blinded lookup and transport/infrastructure records are excluded. The ten-case appendix retains scientific proposals and redacted decision histories for auditability. Public slot identifiers reveal model/condition/sample; original private blinded-case mappings are excluded.

## Cite this work

Use the repository's **Cite this repository** option or [CITATION.cff](CITATION.cff). Please describe the work as an independent exploratory pilot. No SPAR affiliation or peer-reviewed status is claimed.

## Feedback

Specific feedback is welcome on the factual-versus-operative distinction, the materiality threshold and a prospective follow-up testing the notice-writing pattern on fresh scenarios and paraphrases. The current release fixes the rubric and preserves unresolved judgments rather than presenting them as validated ground truth.

## License

The code and authored documentation are licensed under [MIT](LICENSE), copyright 2026 Moyosore Weke. Model-generated responses are identified as research outputs; their inclusion does not assert exclusive human authorship or ownership of third-party material. Model weights and runtimes are not distributed.

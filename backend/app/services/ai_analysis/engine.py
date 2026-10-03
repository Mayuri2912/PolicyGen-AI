"""The analysis pipeline.

    documents -> chunks -> per-chunk findings (LLM) -> merge with permanent ids
              -> cross-document consolidation (LLM) -> deterministic post-processing

Each document is analyzed on its own first, so every finding carries the file
and location it came from.  Consolidation then works on the merged findings
rather than on raw text, which keeps multi-file analyses within the model's
context limit.  Per-chunk results are cached on disk until the job succeeds,
so a retry after a late failure does not repeat the expensive calls.
"""

from __future__ import annotations

import hashlib
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import prompts
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.core.logging import get_logger
from app.core.stages import JobStatus, Stage
from app.database.session import session_scope
from app.models.entities import Analysis, AnalysisFinding, Job, UploadedFile, iso, new_id
from app.schemas.analysis import (
    ASSESSMENT_DISCLAIMER,
    CONTROL_STATUSES,
    GAP_STATUSES,
    PRIORITIES,
    REQUIREMENT_CATEGORIES,
    ChunkFindings,
    ConsolidationResult,
    llm_json_schema,
)
from app.services import storage
from app.services.ai_analysis.chunking import Chunk, chunk_document
from app.services.ai_analysis.providers.base import LLMConfigurationError, LLMError, LLMProvider, LLMRequest
from app.services.ai_analysis.providers.factory import get_provider
from app.services.ai_analysis.safety import EvidenceIndex, new_nonce, scan_for_injection, wrap_untrusted
from app.services.ai_analysis.standards import find_standards
from app.services.ai_analysis.structured import progress_reporter, structured_call
from app.services.file_processing.service import load_document
from app.services.jobs import JobContext, JobFailure, create_job, runner
from app.services.policy_generation.templates import load_templates
from app.services.risk_analysis import mapping, scoring

logger = get_logger("analysis")

ANALYSIS_JOB = "analysis"
KINDS = ("requirement", "risk", "control", "gap", "responsibility", "recommendation")
_PREFIX = {"requirement": "REQ", "risk": "RSK", "control": "CTL", "gap": "GAP", "responsibility": "RSP", "recommendation": "REC"}
_LABEL_TO_CATEGORY = {label.lower(): key for key, label in REQUIREMENT_CATEGORIES.items()}


# ---------------------------------------------------------------- requests
def _system_prompt() -> str:
    return prompts.render("analysis_system")


def build_extract_request(chunk: Chunk) -> LLMRequest:
    schema = llm_json_schema(ChunkFindings)
    user = prompts.render(
        "analysis_extract_user",
        chunk_index=chunk.index,
        chunk_count=chunk.count,
        file_name=chunk.file_name,
        file_type=chunk.file_type,
        document_title=chunk.document_title,
        requirement_categories="\n".join(f"- `{key}`: {label}" for key, label in REQUIREMENT_CATEGORIES.items()),
        risk_scoring=scoring.describe_method(),
        schema=json.dumps(schema),
        nonce=new_nonce(),
        content=wrap_untrusted(chunk.render()),
    )
    payload = {
        "file_name": chunk.file_name,
        "file_type": chunk.file_type,
        "document_title": chunk.document_title,
        "chunk_index": chunk.index,
        "chunk_count": chunk.count,
        "units": [
            {
                "kind": u.kind,
                "heading": u.heading,
                "location": u.location,
                "text": u.text,
                "headers": u.headers,
                "rows": u.rows,
                "first_row": u.first_row,
            }
            for u in chunk.units
        ],
    }
    return LLMRequest("extract_findings", _system_prompt(), user, schema, payload, max_tokens=get_settings().anthropic_max_output_tokens)


def build_consolidate_request(merged: dict, documents: list[dict]) -> LLMRequest:
    schema = llm_json_schema(ConsolidationResult)
    policy_types = [
        {"id": t.id, "name": t.name, "description": t.description, "keywords": list(t.keywords)}
        for t in load_templates().values()
    ]

    def compact(item: dict, *fields: str) -> dict:
        out = {"ref_id": item["ref_id"], **{f: item.get(f) for f in fields}}
        out["file_name"] = item.get("source", {}).get("file_name", "")
        return out

    payload = {
        "documents": documents,
        "organization_name": merged["organization_name"],
        "requirements": [compact(r, "statement", "category", "category_label", "basis") for r in merged["requirement"]],
        "risks": [
            compact(r, "title", "description", "category", "origin", "likelihood", "impact", "score", "level",
                    "scoring_basis", "existing_controls", "recommended_mitigation")
            for r in merged["risk"]
        ],
        "controls": [
            compact(c, "name", "description", "control_type", "control_function", "status", "basis",
                    "related_requirements", "related_risks", "recommended_action")
            for c in merged["control"]
        ],
        "responsibilities": [
            {"role": r["role"], "responsibility": r["responsibility"], "basis": r["basis"], "file_name": r["source"]["file_name"]}
            for r in merged["responsibility"]
        ],
        "facts": merged["facts"],
        "policy_types": policy_types,
    }
    findings = {k: v for k, v in payload.items() if k != "policy_types"}
    user = prompts.render(
        "analysis_consolidate_user",
        document_count=len(documents),
        policy_types="\n".join(f"- `{p['id']}`: {p['name']} - {p['description']}" for p in policy_types),
        schema=json.dumps(schema),
        nonce=new_nonce(),
        findings=wrap_untrusted(json.dumps(findings, ensure_ascii=False, indent=1)),
    )
    return LLMRequest("consolidate", _system_prompt(), user, schema, payload, max_tokens=get_settings().anthropic_max_output_tokens)


# ------------------------------------------------------------------- merge
def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _category(value: str) -> str:
    value = (value or "").strip().lower()
    if value in REQUIREMENT_CATEGORIES:
        return value
    return _LABEL_TO_CATEGORY.get(value.replace("_", " "), "organizational")


def merge_findings(results: list[tuple[Chunk, ChunkFindings]], index: EvidenceIndex) -> dict:
    """Combine per-chunk findings, assign permanent ids and verify evidence."""
    merged: dict = {"requirement": [], "risk": [], "control": [], "responsibility": [], "facts": [], "organization_name": ""}
    seen: dict[tuple, dict] = {}
    unverified = 0

    def source(chunk: Chunk, location: str, evidence: str) -> dict:
        nonlocal unverified
        verified = index.verify(chunk.file_name, evidence) if evidence else False
        if evidence and not verified:
            unverified += 1
        return {
            "document_id": chunk.document_id,
            "file_id": chunk.file_id,
            "file_name": chunk.file_name,
            "location": location,
            "evidence": evidence,
            "evidence_verified": verified,
        }

    def add(kind: str, key: tuple, item: dict) -> tuple[dict, bool]:
        existing = seen.get((kind, *key))
        if existing is not None:
            src = item["source"]
            if src["file_name"] != existing["source"]["file_name"] or src["location"] != existing["source"]["location"]:
                existing.setdefault("additional_sources", []).append(src)
            return existing, False
        item["ref_id"] = f"{_PREFIX[kind]}-{len(merged[kind]) + 1:03d}"
        merged[kind].append(item)
        seen[(kind, *key)] = item
        return item, True

    for chunk, findings in results:
        if findings.organization_name and not merged["organization_name"]:
            # Only accept a name that actually appears in the source text.
            if index.contains(findings.organization_name):
                merged["organization_name"] = findings.organization_name

        requirement_ids: dict[str, str] = {}
        for r in findings.requirements:
            category = _category(r.category)
            item, _ = add(
                "requirement",
                (_norm(r.statement),),
                {
                    "statement": r.statement,
                    "category": category,
                    "category_label": REQUIREMENT_CATEGORIES[category],
                    "basis": r.basis,
                    "confidence": r.confidence,
                    "source": source(chunk, r.source_location, r.evidence),
                },
            )
            requirement_ids[r.local_id] = item["ref_id"]

        risk_ids: dict[str, str] = {}
        for k in findings.risks:
            key = (_norm(k.title), k.origin) if k.origin == "inferred" else (_norm(k.title), k.origin, _norm(k.description))
            item, created = add(
                "risk",
                key,
                scoring.apply_scores(
                    {
                        "title": k.title,
                        "description": k.description,
                        "category": k.category.strip() or "Uncategorized",
                        "origin": k.origin,
                        "likelihood": k.likelihood,
                        "impact": k.impact,
                        "scoring_basis": k.scoring_basis if (k.likelihood and k.impact) else "estimated",
                        "existing_controls": k.existing_controls,
                        "recommended_mitigation": k.recommended_mitigation,
                        "residual_likelihood": k.residual_likelihood,
                        "residual_impact": k.residual_impact,
                        "confidence": k.confidence,
                        "source": source(chunk, k.source_location, k.evidence),
                    }
                ),
            )
            risk_ids[k.local_id] = item["ref_id"]

        for c in findings.controls:
            key = (_norm(c.name), c.basis) if c.basis == "recommended" else (_norm(c.name), c.basis, _norm(c.description))
            related_requirements = [requirement_ids[i] for i in c.related_requirements if i in requirement_ids]
            related_risks = [risk_ids[i] for i in c.related_risks if i in risk_ids]
            item, created = add(
                "control",
                key,
                {
                    "name": c.name,
                    "objective": c.objective,
                    "description": c.description,
                    "control_type": c.control_type,
                    "control_function": c.control_function,
                    "status": c.status if c.basis == "existing" else "not_implemented",
                    "status_label": CONTROL_STATUSES[c.status if c.basis == "existing" else "not_implemented"],
                    "basis": c.basis,
                    "owner": c.owner,
                    "related_requirements": related_requirements,
                    "related_risks": related_risks,
                    "recommended_action": c.recommended_action,
                    "confidence": c.confidence,
                    "source": source(chunk, c.source_location, c.evidence),
                },
            )
            if not created:
                item["related_requirements"] = list(dict.fromkeys([*item["related_requirements"], *related_requirements]))
                item["related_risks"] = list(dict.fromkeys([*item["related_risks"], *related_risks]))

        for r in findings.responsibilities:
            add(
                "responsibility",
                (_norm(r.role), _norm(r.responsibility)),
                {"role": r.role, "responsibility": r.responsibility, "basis": r.basis, "source": source(chunk, r.source_location, r.evidence)},
            )

        for fact in findings.facts:
            merged["facts"].append(
                {
                    "topic": fact.topic,
                    "value": fact.value,
                    "file_name": chunk.file_name,
                    "source_location": fact.source_location,
                    "evidence": fact.evidence,
                }
            )

    merged["unverified_evidence"] = unverified
    return merged


# --------------------------------------------------------------- lifecycle
def start_analysis(db: Session, file_ids: list[str]) -> tuple[Analysis, Job]:
    """Validate the request and queue an analysis job (or return the active one)."""
    file_ids = list(dict.fromkeys(file_ids))
    if not file_ids:
        raise ValidationFailed("Select at least one file to analyze.")
    files = db.scalars(select(UploadedFile).where(UploadedFile.id.in_(file_ids))).all()
    found = {f.id: f for f in files}
    missing = [i for i in file_ids if i not in found]
    if missing:
        raise NotFoundError("One or more selected files no longer exist.", details={"file_ids": missing})
    not_ready = [found[i].original_name for i in file_ids if found[i].status != Stage.EXTRACTED.value or found[i].document is None]
    if not_ready:
        raise ConflictError(
            "These files have not been extracted successfully and cannot be analyzed: " + ", ".join(not_ready) + ".",
            code="files_not_ready",
        )

    analysis_id = new_id()
    digest = hashlib.sha256(",".join(sorted(file_ids)).encode()).hexdigest()[:40]
    job, created = create_job(
        db,
        ANALYSIS_JOB,
        stage=Stage.EXTRACTED,
        payload={"subject_id": analysis_id, "file_ids": file_ids},
        dedupe_key=f"analysis:{digest}",
        message="Waiting to start analysis.",
    )
    if not created:
        # The same files are already being analyzed: hand back that run.
        return db.get(Analysis, job.payload["subject_id"]), job

    analysis = Analysis(
        id=analysis_id,
        job_id=job.id,
        status=Stage.ANALYZING.value,
        source_files=[
            {
                "file_id": found[i].id,
                "file_name": found[i].original_name,
                "file_type": found[i].extension,
                "document_id": found[i].document.id,
            }
            for i in file_ids
        ],
    )
    db.add(analysis)
    db.commit()
    runner.submit(job.id)
    # In inline mode the job has already run; report its real state.
    db.refresh(job)
    db.refresh(analysis)
    return analysis, job


def _chunk_key(provider: LLMProvider, chunk: Chunk) -> str:
    return hashlib.sha256(f"{provider.name}|{provider.model}|{chunk.file_name}|{chunk.render()}".encode()).hexdigest()


def run_analysis(ctx: JobContext) -> dict:
    settings = get_settings()
    analysis_id = ctx.payload["subject_id"]
    try:
        provider = get_provider()
    except LLMConfigurationError as exc:
        raise JobFailure(exc.message, exc.code) from exc

    ctx.update(stage=Stage.ANALYZING, progress=3, message="Preparing documents.")
    documents = []
    with session_scope() as db:
        analysis = db.get(Analysis, analysis_id)
        if analysis is None:
            raise JobFailure("The analysis no longer exists.", "not_found")
        analysis.status = Stage.ANALYZING.value
        analysis.error_message = None
        analysis.provider, analysis.model, analysis.simulated = provider.name, provider.model, provider.simulated
        for entry in analysis.source_files:
            file = db.get(UploadedFile, entry["file_id"])
            if file is None:
                raise JobFailure(f"'{entry['file_name']}' was deleted before it could be analyzed.", "missing_file")
            documents.append(load_document(file))

    # A provider may need smaller parts than the general limit (slow or small models).
    chunk_limit = min(settings.chunk_max_chars, provider.chunk_max_chars or settings.chunk_max_chars)
    index = EvidenceIndex()
    security_flags, chunks, standards = [], [], []
    for document in documents:
        index.add(document.file_name, document.extracted_text)
        security_flags += scan_for_injection(document.extracted_text, document.file_name)
        standards += [{"name": name, "file_name": document.file_name} for name in find_standards(document.extracted_text)]
        chunks += chunk_document(document, chunk_limit)
    if len(chunks) > settings.max_chunks_per_analysis:
        raise JobFailure(
            f"These documents need {len(chunks)} AI requests, above the configured limit of "
            f"{settings.max_chunks_per_analysis}. Analyze fewer files at once, or raise MAX_CHUNKS_PER_ANALYSIS.",
            "too_large",
        )

    cache_file = storage.cache_path(f"analysis_{analysis_id}.json")
    cache: dict = storage.read_json(cache_file) if cache_file.exists() else {}
    results: list[tuple[Chunk, ChunkFindings]] = []
    failed_chunks: list[dict] = []
    last_error: LLMError | None = None
    for position, chunk in enumerate(chunks):
        activity = f"Analyzing {chunk.file_name}" + (f" (part {chunk.index} of {chunk.count})" if chunk.count > 1 else "") + "."
        ctx.update(progress=5 + int(70 * position / len(chunks)), message=activity)
        key = _chunk_key(provider, chunk)
        try:
            if key in cache:
                findings = ChunkFindings.model_validate(cache[key])
            else:
                extract = build_extract_request(chunk)
                extract.observer = progress_reporter(ctx.update, activity)
                findings, _ = structured_call(provider, extract, ChunkFindings, repair_attempts=settings.llm_json_repair_attempts)
                cache[key] = findings.model_dump()
                storage.write_json(cache_file, cache)
        except LLMConfigurationError as exc:
            raise JobFailure(exc.message, exc.code) from exc
        except LLMError as exc:
            if exc.code == "llm_quota_exceeded":
                # Every further request would fail the same way and count against the
                # account.  Finished segments stay cached and are reused on retry.
                done = f" {len(results)} of {len(chunks)} document segment(s) were analyzed and are kept for the retry." if results else ""
                raise JobFailure(f"AI analysis stopped. {exc.message}{done}", exc.code) from exc
            # One failed segment must not sink the others; it is reported.
            last_error = exc
            failed_chunks.append({"file_name": chunk.file_name, "part": chunk.index, "of": chunk.count, "reason": exc.message})
            logger.warning("chunk analysis failed", extra={"analysis_id": analysis_id, "code": exc.code, "part": chunk.index})
            continue
        results.append((chunk, findings))

    if not results:
        reason = last_error.message if last_error else "No content could be analyzed."
        raise JobFailure(f"AI analysis failed for every document segment. {reason}", last_error.code if last_error else "analysis_failed")

    merged = merge_findings(results, index)
    doc_overviews = []
    for document in documents:
        own = [f for c, f in results if c.document_id == document.document_id]
        doc_overviews.append(
            {
                "file_name": document.file_name,
                "title": document.document_title,
                "purpose": " ".join(dict.fromkeys(f.document_purpose for f in own if f.document_purpose))[:800],
                "category": next((f.document_category for f in own if f.document_category), ""),
            }
        )

    ctx.update(progress=80, message="Consolidating findings across documents.")
    try:
        consolidate = build_consolidate_request(merged, doc_overviews)
        consolidate.observer = progress_reporter(ctx.update, "Consolidating findings across documents.")
        consolidation, _ = structured_call(provider, consolidate, ConsolidationResult, repair_attempts=settings.llm_json_repair_attempts)
    except LLMError as exc:
        raise JobFailure(f"The findings were extracted, but consolidating them failed. {exc.message}", exc.code) from exc

    ctx.update(progress=92, message="Scoring risks and mapping controls.")
    stats = _store_results(analysis_id, merged, consolidation, results, security_flags, standards, failed_chunks, len(chunks), doc_overviews)
    storage.remove(cache_file)
    ctx.update(stage=Stage.ANALYSIS_COMPLETED, progress=100, message="Analysis completed.")
    logger.info("analysis completed", extra={"analysis_id": analysis_id, "provider": provider.name, **stats})
    return {"analysis_id": analysis_id, **stats}


def _store_results(
    analysis_id, merged, consolidation: ConsolidationResult, results, security_flags, standards, failed_chunks, chunk_total, doc_overviews
) -> dict:
    requirements, risks, controls = merged["requirement"], merged["risk"], merged["control"]
    valid = {
        "requirement": {r["ref_id"] for r in requirements},
        "risk": {r["ref_id"] for r in risks},
        "control": {c["ref_id"] for c in controls},
    }
    warnings: list[str] = []

    gaps = [g.model_dump() for g in consolidation.gap_analysis]
    for position, gap in enumerate(gaps, start=1):
        gap["ref_id"] = f"GAP-{position:03d}"
    warnings += mapping.clean_references(gaps, {"associated_risk_ids": valid["risk"], "recommended_control_ids": valid["control"]})
    gaps = mapping.complete_gap_analysis(gaps, requirements)
    for position, gap in enumerate(gaps, start=1):
        gap["ref_id"] = f"GAP-{position:03d}"

    recommendations = [r.model_dump() for r in consolidation.recommendations]
    for position, item in enumerate(recommendations, start=1):
        item["ref_id"] = f"REC-{position:03d}"
    all_ids = valid["requirement"] | valid["risk"] | valid["control"]
    warnings += mapping.clean_references(recommendations, {"related_ids": all_ids})

    if merged["unverified_evidence"]:
        warnings.append(
            f"{merged['unverified_evidence']} finding(s) cite evidence that could not be matched to the source text. "
            "They are marked as unverified; check them before relying on them."
        )
    if failed_chunks:
        warnings.append(f"{len(failed_chunks)} of {chunk_total} document segment(s) could not be analyzed and are not reflected in the findings.")

    templates = load_templates()
    pick = consolidation.recommended_policy_type.strip().lower()
    if pick not in templates:
        if pick:
            warnings.append(f"The analysis suggested an unknown policy type ('{pick}'); the recommendation falls back to keyword evidence.")
        pick = ""

    chunk_findings = [f for _, f in results]
    summary = {
        "overall_summary": consolidation.overall_summary,
        "document_summaries": [d.model_dump() for d in consolidation.document_summaries],
        "document_category": consolidation.document_category,
        "document_titles": [d["title"] for d in doc_overviews if d.get("title")],
        "subject_areas": consolidation.subject_areas,
        "organization_name": merged["organization_name"],
        "stakeholders": sorted({s for f in chunk_findings for s in f.stakeholders if s})[:40],
        "systems": sorted({s for f in chunk_findings for s in f.systems if s})[:40],
        "policy_recommendation": {
            "policy_type": pick,
            "rationale": consolidation.recommendation_rationale,
            "alternatives": [a.model_dump() for a in consolidation.alternative_policy_types if a.policy_type.strip().lower() in templates],
        },
        "contradictions": [c.model_dump() for c in consolidation.contradictions],
        "missing_information": [m.model_dump() for m in consolidation.missing_information],
        "assumptions": consolidation.assumptions,
        "facts": merged["facts"],
        "standards_mentioned": standards,
        "security_flags": security_flags,
        "processing": {
            "chunks_total": chunk_total,
            "chunks_failed": len(failed_chunks),
            "failed_chunks": failed_chunks,
            "warnings": warnings,
        },
    }

    rows = {
        "requirement": requirements,
        "risk": risks,
        "control": controls,
        "gap": gaps,
        "responsibility": merged["responsibility"],
        "recommendation": recommendations,
    }
    with session_scope() as db:
        analysis = db.get(Analysis, analysis_id)
        for finding in list(analysis.findings):  # a retry replaces earlier partial results
            db.delete(finding)
        db.flush()
        position = 0
        for kind, items in rows.items():
            for item in items:
                position += 1
                ref_id = item.pop("ref_id")
                db.add(
                    AnalysisFinding(
                        analysis_id=analysis_id, kind=kind, ref_id=ref_id, position=position, data=item, original_data=dict(item)
                    )
                )
        analysis.summary = summary
        analysis.status = Stage.ANALYSIS_COMPLETED.value
    return {kind + "s": len(items) for kind, items in rows.items()}


def on_analysis_failed(db: Session, job: Job, message: str, code: str) -> None:
    analysis = db.get(Analysis, (job.payload or {}).get("subject_id"))
    if analysis is not None:
        analysis.status = (Stage.CANCELLED if job.status == JobStatus.CANCELLED.value else Stage.FAILED).value
        analysis.error_message = message


runner.register(ANALYSIS_JOB, run_analysis, on_analysis_failed)


# ---------------------------------------------------------- reading results
def findings_by_kind(analysis: Analysis, *, include_excluded: bool = True) -> dict[str, list[dict]]:
    """Findings grouped by kind as plain dicts carrying their ids and edit flags."""
    grouped: dict[str, list[dict]] = {kind: [] for kind in KINDS}
    # A gap entry assesses one requirement, so excluding the requirement
    # removes its gap entry from everything built on the active findings.
    excluded_requirements = {f.ref_id for f in analysis.findings if f.kind == "requirement" and f.excluded}
    for finding in analysis.findings:
        if not include_excluded and (
            finding.excluded or (finding.kind == "gap" and finding.data.get("requirement_id") in excluded_requirements)
        ):
            continue
        item = {
            "id": finding.id,
            "ref_id": finding.ref_id,
            **finding.data,
            "user_edited": finding.user_edited,
            "excluded": finding.excluded,
        }
        if finding.user_edited:
            item["original"] = finding.original_data
        grouped[finding.kind].append(item)
    return grouped


def serialize_analysis(analysis: Analysis) -> dict:
    """Full analysis payload for the API, with statistics over active findings."""
    summary = analysis.summary or {}
    everything = findings_by_kind(analysis)
    active = findings_by_kind(analysis, include_excluded=False)
    references = [
        {
            "ref_id": item["ref_id"],
            "kind": kind,
            "title": item.get("statement") or item.get("title") or item.get("name") or item.get("role", ""),
            **{k: src.get(k) for k in ("file_name", "location", "evidence", "evidence_verified")},
        }
        for kind in ("requirement", "risk", "control", "responsibility")
        for item in active[kind]
        for src in [item.get("source", {}), *item.get("additional_sources", [])]
        if src.get("file_name")
    ]
    return {
        "id": analysis.id,
        "job_id": analysis.job_id,
        "status": analysis.status,
        "error_message": analysis.error_message,
        "provider": analysis.provider,
        "model": analysis.model,
        "simulated": analysis.simulated,
        "created_at": iso(analysis.created_at),
        "updated_at": iso(analysis.updated_at),
        "source_files": analysis.source_files,
        "summary": {
            k: summary.get(k)
            for k in ("overall_summary", "document_summaries", "document_category", "subject_areas", "organization_name", "stakeholders", "systems")
        },
        "requirements": everything["requirement"],
        "risks": everything["risk"],
        "controls": everything["control"],
        "gaps": everything["gap"],
        "responsibilities": everything["responsibility"],
        "recommendations": everything["recommendation"],
        "contradictions": summary.get("contradictions", []),
        "missing_information": summary.get("missing_information", []),
        "assumptions": summary.get("assumptions", []),
        "standards_mentioned": summary.get("standards_mentioned", []),
        "security_flags": summary.get("security_flags", []),
        "processing": summary.get("processing", {}),
        "source_references": references,
        "risk_summary": scoring.summarize_risks(active["risk"]),
        "gap_summary": mapping.summarize_gaps(active["gap"]),
        "control_summary": mapping.summarize_controls(active["control"]),
        "control_matrix": mapping.control_matrix(active["requirement"], active["risk"], active["control"], active["gap"]),
        "scoring_method": scoring.scoring_method(),
        "disclaimer": ASSESSMENT_DISCLAIMER,
    }


# ------------------------------------------------------------ user editing
_EDITABLE = {
    "requirement": {"statement", "category"},
    "risk": {"title", "description", "category", "likelihood", "impact", "recommended_mitigation"},
    "control": {"name", "description", "status", "owner", "recommended_action"},
    "gap": {"status", "identified_gap", "priority", "suggested_action"},
    "responsibility": {"role", "responsibility"},
    "recommendation": {"title", "description", "priority"},
}
_MAX_TEXT = 2000


def update_finding(db: Session, finding: AnalysisFinding, *, data: dict | None, excluded: bool | None, restore: bool) -> AnalysisFinding:
    """Apply a user edit, exclusion or restore to one finding.

    The original AI output is kept in ``original_data`` so edits stay
    distinguishable and can be undone.
    """
    if restore:
        finding.data = dict(finding.original_data)
        finding.user_edited = False
        finding.excluded = False
    if excluded is not None:
        finding.excluded = excluded
    if data:
        allowed = _EDITABLE[finding.kind]
        unknown = set(data) - allowed
        if unknown:
            raise ValidationFailed(f"These fields cannot be edited on a {finding.kind}: {', '.join(sorted(unknown))}.")
        updated = dict(finding.data)
        for key, value in data.items():
            if key in ("likelihood", "impact"):
                if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
                    raise ValidationFailed(f"{key.title()} must be a whole number from 1 to 5.")
            elif not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
                raise ValidationFailed(f"'{key}' must be non-empty text of at most {_MAX_TEXT} characters.")
            else:
                value = value.strip()
            updated[key] = value

        if finding.kind == "requirement":
            if updated["category"] not in REQUIREMENT_CATEGORIES:
                raise ValidationFailed("Unknown requirement category.")
            updated["category_label"] = REQUIREMENT_CATEGORIES[updated["category"]]
        elif finding.kind == "risk":
            if {"likelihood", "impact"} & set(data):
                updated["scoring_basis"] = "user"
            updated = scoring.apply_scores(updated)
        elif finding.kind == "control":
            if updated["status"] not in CONTROL_STATUSES:
                raise ValidationFailed("Unknown control status.")
            updated["status_label"] = CONTROL_STATUSES[updated["status"]]
        elif finding.kind == "gap":
            if updated["status"] not in GAP_STATUSES:
                raise ValidationFailed("Unknown gap status.")
            updated["status_label"] = GAP_STATUSES[updated["status"]]
        if finding.kind in ("gap", "recommendation") and updated.get("priority") not in PRIORITIES:
            raise ValidationFailed("Priority must be one of: " + ", ".join(PRIORITIES) + ".")

        finding.data = updated
        finding.user_edited = updated != finding.original_data
    db.commit()
    return finding


def delete_analysis(db: Session, analysis: Analysis) -> None:
    """Delete an analysis with its findings, derived reports and their files."""
    for policy in analysis.policies:
        storage.remove_report_files(policy.id)
    storage.remove(storage.cache_path(f"analysis_{analysis.id}.json"))
    db.delete(analysis)
    db.commit()

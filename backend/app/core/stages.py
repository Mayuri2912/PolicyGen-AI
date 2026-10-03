"""Processing state machine shared by files, jobs and policies.

``Stage`` is where a piece of work currently is in the pipeline; ``JobStatus``
is the lifecycle of the background job driving it.  ``advance`` rejects any
transition that is not in ``TRANSITIONS`` so a failed task can never be
reported as completed by accident.
"""

from __future__ import annotations

from enum import Enum


class Stage(str, Enum):
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    ANALYZING = "analyzing"
    ANALYSIS_COMPLETED = "analysis_completed"
    GENERATING_POLICY = "generating_policy"
    FORMATTING_DOCUMENT = "formatting_document"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}

STAGE_LABELS = {
    Stage.UPLOADED: "Uploaded",
    Stage.VALIDATING: "Validating",
    Stage.EXTRACTING: "Extracting",
    Stage.EXTRACTED: "Extracted",
    Stage.ANALYZING: "Analyzing",
    Stage.ANALYSIS_COMPLETED: "Analysis Completed",
    Stage.GENERATING_POLICY: "Generating Policy",
    Stage.FORMATTING_DOCUMENT: "Formatting Document",
    Stage.COMPLETED: "Completed",
    Stage.FAILED: "Failed",
    Stage.CANCELLED: "Cancelled",
}

_ALWAYS = {Stage.FAILED, Stage.CANCELLED}

TRANSITIONS: dict[Stage, set[Stage]] = {
    Stage.UPLOADED: {Stage.VALIDATING, Stage.EXTRACTING} | _ALWAYS,
    Stage.VALIDATING: {Stage.EXTRACTING} | _ALWAYS,
    Stage.EXTRACTING: {Stage.EXTRACTED} | _ALWAYS,
    Stage.EXTRACTED: {Stage.ANALYZING, Stage.VALIDATING, Stage.EXTRACTING} | _ALWAYS,
    Stage.ANALYZING: {Stage.ANALYSIS_COMPLETED} | _ALWAYS,
    Stage.ANALYSIS_COMPLETED: {Stage.GENERATING_POLICY, Stage.ANALYZING} | _ALWAYS,
    Stage.GENERATING_POLICY: {Stage.FORMATTING_DOCUMENT} | _ALWAYS,
    Stage.FORMATTING_DOCUMENT: {Stage.COMPLETED} | _ALWAYS,
    Stage.COMPLETED: {Stage.GENERATING_POLICY, Stage.FORMATTING_DOCUMENT},
    # A failed or cancelled item may be retried from the stage it started at.
    Stage.FAILED: {Stage.VALIDATING, Stage.EXTRACTING, Stage.ANALYZING, Stage.GENERATING_POLICY, Stage.FORMATTING_DOCUMENT},
    Stage.CANCELLED: {Stage.VALIDATING, Stage.EXTRACTING, Stage.ANALYZING, Stage.GENERATING_POLICY, Stage.FORMATTING_DOCUMENT},
}


class InvalidTransition(ValueError):
    pass


def can_transition(current: Stage | str, target: Stage | str) -> bool:
    current, target = Stage(current), Stage(target)
    return current == target or target in TRANSITIONS[current]


def advance(current: Stage | str, target: Stage | str) -> Stage:
    if not can_transition(current, target):
        raise InvalidTransition(f"Cannot move from '{Stage(current).value}' to '{Stage(target).value}'.")
    return Stage(target)

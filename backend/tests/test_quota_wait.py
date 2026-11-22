from datetime import timedelta
from unittest.mock import Mock

from app.core.stages import JobStatus, Stage
from app.database.session import session_scope
from app.models.entities import Analysis, Job, utcnow
from app.services import jobs
from app.services.ai_analysis.providers.base import LLMRateLimitError


def test_quota_failure_schedules_same_job_and_does_not_run_early(settings, monkeypatch):
    monkeypatch.setattr(settings, "job_execution_mode", "thread")
    runner = jobs.JobRunner()
    schedule = Mock()
    monkeypatch.setattr(runner, "schedule", schedule)
    def handler(ctx):
        try:
            raise LLMRateLimitError("exhausted", retry_after_seconds=3600, code="llm_providers_unavailable")
        except LLMRateLimitError as exc:
            raise jobs.JobFailure(exc.message, exc.code) from exc
    handler_mock = Mock(side_effect=handler)
    runner.register("quota-test", handler_mock)
    with session_scope() as db:
        job = Job(type="quota-test", stage=Stage.ANALYZING.value, payload={})
        db.add(job)
        db.flush()
        job_id = job.id
    runner._run(job_id)
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job.status == "queued"
        assert job.result["next_retry_at"] and job.error_message is None
        assert job.result["quota_deferrals"] == 1
    schedule.assert_called_once_with(job_id, 3600)
    runner._run(job_id)
    assert handler_mock.call_count == 1


def test_restart_preserves_waiting_analysis_and_workspace(monkeypatch):
    scheduled = Mock()
    monkeypatch.setattr(jobs.runner, "schedule", scheduled)
    with session_scope() as db:
        analysis = Analysis(status=Stage.ANALYZING.value, workspace_id="customer-test")
        db.add(analysis)
        db.flush()
        job = Job(type="analysis", status="queued", stage=Stage.ANALYZING.value,
            workspace_id="customer-test", payload={"subject_id": analysis.id},
            result={"next_retry_at": (utcnow() + timedelta(hours=1)).isoformat(), "quota_deferrals": 1})
        db.add(job)
        db.flush()
        analysis_id, job_id = analysis.id, job.id
    assert jobs.recover_interrupted_jobs() == 0
    with session_scope() as db:
        assert db.get(Job, job_id).status == "queued"
        assert db.get(Analysis, analysis_id).status == Stage.ANALYZING.value
    assert scheduled.call_args.args[0] == job_id


def test_cancel_waiting_job_is_immediate():
    with session_scope() as db:
        job = Job(type="test", status="queued", stage=Stage.ANALYZING.value,
            result={"next_retry_at": (utcnow() + timedelta(hours=1)).isoformat()})
        db.add(job)
        db.flush()
        jobs.request_cancel(db, job)
        assert job.status == JobStatus.CANCELLED.value


def test_quota_deferrals_are_bounded(settings, monkeypatch):
    monkeypatch.setattr(settings, "job_execution_mode", "thread")
    runner = jobs.JobRunner()
    with session_scope() as db:
        job = Job(type="test", status="running", stage=Stage.ANALYZING.value, result={"quota_deferrals": 3})
        db.add(job)
        db.flush()
        job_id = job.id
    assert runner._defer_quota(job_id, jobs.JobFailure("quota", "llm_quota_exceeded")) is False


def test_new_credentials_resume_old_quota_wait(monkeypatch):
    schedule = Mock()
    monkeypatch.setattr(jobs.runner, "schedule", schedule)
    with session_scope() as db:
        job = Job(type="analysis", status="queued", stage=Stage.ANALYZING.value, payload={},
            result={"next_retry_at": (utcnow() + timedelta(hours=5)).isoformat(), "quota_deferrals": 3, "quota_config": "old-config"})
        db.add(job)
        db.flush()
        job_id = job.id
    jobs.recover_interrupted_jobs()
    assert schedule.call_args.args[0] == job_id
    assert schedule.call_args.args[1] < 2
    with session_scope() as db:
        assert db.get(Job, job_id).result["quota_deferrals"] == 0

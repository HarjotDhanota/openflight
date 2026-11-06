"""D19: subsequent shots cannot cancel late metrics; worker has one active job."""

from pathlib import Path
from types import SimpleNamespace

from openflight.camera import impact_live as live
from openflight.camera.impact_live import Dispatcher


class Runner:
    def __init__(self):
        self.jobs = []
        self.cancelled = []

    def submit(self, body):
        self.jobs.append(body)

    def cancel_through(self, job_id):
        self.cancelled.append(job_id)

    def stop(self):
        pass


def setup():
    runner = Runner()
    published = []
    dispatcher = Dispatcher(
        lambda _: runner,
        lambda shot, reading, session: published.append((shot.shot_number, reading, session)),
        lambda *_: None,
    )
    return dispatcher, runner, published


def test_next_shot_retains_late_reading_and_queues_second_job_until_first_finishes():
    dispatcher, runner, published = setup()
    try:
        first = dispatcher.submit(
            SimpleNamespace(shot_number=1), {"session_uuid": "s", "shot_number": 1}
        )
        dispatcher.next_shot_arrived()
        second = dispatcher.submit(
            SimpleNamespace(shot_number=2), {"session_uuid": "s", "shot_number": 2}
        )
        assert published == [] and runner.cancelled == []
        assert [j["job_id"] for j in runner.jobs] == [first]
        dispatcher.on_result({"job_id": first, "reading": {"status": "ok", "impact_offset_mm": 5}})
        assert [j["job_id"] for j in runner.jobs] == [first, second]
        assert published[0][0] == 1 and published[0][1]["impact_offset_mm"] == 5
    finally:
        dispatcher.stop()


def test_backlog_is_spooled_fifo_and_worker_has_only_one_active_command():
    dispatcher, runner, published = setup()
    try:
        jobs = [
            dispatcher.submit(
                SimpleNamespace(shot_number=i),
                {"session_uuid": "s", "shot_number": i, "snapshot": "x" * 4000},
            )
            for i in range(1, 101)
        ]
        assert len(runner.jobs) == 1
        assert len(list(Path(dispatcher._spool.name).glob("*.json"))) == 99
        for j in jobs:
            dispatcher.on_result({"job_id": j, "reading": {"status": "ok"}})
            assert len(runner.jobs) <= len(published) + 1
        assert [p[0] for p in published] == list(range(1, 101))
        assert not list(Path(dispatcher._spool.name).glob("*.json"))
    finally:
        dispatcher.stop()


def test_worker_exit_refuses_only_active_job_and_dispatches_waiting_job():
    runners, published = [], []

    def factory(_):
        runner = Runner()
        runners.append(runner)
        return runner

    dispatcher = Dispatcher(
        factory, lambda shot, r, _: published.append((shot.shot_number, r)), lambda *_: None
    )
    try:
        first = dispatcher.submit(SimpleNamespace(shot_number=1), {"shot_number": 1})
        second = dispatcher.submit(SimpleNamespace(shot_number=2), {"shot_number": 2})
        dispatcher.on_result({"type": "worker_exited", "runner": runners[0]})
        assert published[0][0] == 1 and published[0][1]["reason_code"] == "worker_stopped"
        assert runners[1].jobs[0]["job_id"] == second
        dispatcher.on_result({"job_id": first, "reading": {"status": "ok"}})
        dispatcher.on_result({"type": "worker_exited", "runner": runners[0]})
        assert len(published) == 1
        dispatcher.on_result({"job_id": second, "reading": {"status": "ok"}})
        assert [p[0] for p in published] == [1, 2]
    finally:
        dispatcher.stop()


def test_shutdown_refuses_all_outstanding_jobs_and_late_results_are_ignored():
    dispatcher, runner, published = setup()
    first = dispatcher.submit(SimpleNamespace(shot_number=1), {"shot_number": 1})
    dispatcher.submit(SimpleNamespace(shot_number=2), {"shot_number": 2})
    dispatcher.stop()
    assert len(published) == 2 and all(p[1]["reason_code"] == "worker_stopped" for p in published)
    dispatcher.on_result({"job_id": first, "reading": {"status": "ok"}})
    assert len(published) == 2


def test_invalid_or_future_results_cannot_finish_waiting_job():
    dispatcher, runner, published = setup()
    try:
        first = dispatcher.submit(SimpleNamespace(shot_number=1), {"shot_number": 1})
        second = dispatcher.submit(SimpleNamespace(shot_number=2), {"shot_number": 2})
        for job_id in (True, "1", second):
            dispatcher.on_result({"job_id": job_id, "reading": {"status": "ok"}})
        dispatcher.on_result({"type": "worker_exited"})
        assert not published and len(runner.jobs) == 1
        dispatcher.on_result({"job_id": first, "reading": []})
        assert published[0][1]["reason_code"] == "analysis_failed_invalid_worker_reading"
        assert runner.jobs[-1]["job_id"] == second
    finally:
        dispatcher.stop()


def test_nonobject_worker_json_does_not_kill_result_drain(monkeypatch):
    received = []
    runner = live.ProcessRunner.__new__(live.ProcessRunner)
    runner._on_result = received.append
    monkeypatch.setattr(
        live, "read_lines", lambda _: iter(["[]", '{"type":"result","job_id":1,"reading":{}}'])
    )
    runner._read(SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: 1)))
    assert [r["type"] for r in received] == ["result", "worker_exited"]


def test_shutdown_is_terminal_and_warming_does_not_restart_a_worker():
    runners = []

    def factory(_):
        runners.append(Runner())
        return runners[-1]

    dispatcher = Dispatcher(factory, lambda *_: None, lambda *_: None)
    dispatcher.warm()
    dispatcher.stop()
    dispatcher.warm()
    assert len(runners) == 1

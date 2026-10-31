"""Selection, feasibility gates and live native publication policies."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight.camera import trigger_policy as policy
from openflight.camera.native_fusion import NativeFusionExtension
from openflight.rolling_buffer.processor import RollingBufferProcessor
from tests.test_free_play import Jobs
from tests.test_native_fusion import bundle
from tests.test_tester_server import eligible_app, setup_saw_ball


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"capture_mode": "native_internal"}, "independent"),
        ({"trigger_policy": "independent"}, "independent"),
        ({"trigger_policy": "ops_led"}, "ops_led"),
        ({"trigger_policy": "hybrid"}, "hybrid"),
    ],
)
def test_legacy_and_explicit_selection(payload, expected):
    assert policy.selection(payload, "free_play", Path("sealed")) == expected


@pytest.mark.parametrize(
    "payload,message",
    [
        ({}, "Choose a trigger architecture"),
        ({"trigger_policy": "sound"}, "sound trigger"),
        ({"capture_mode": "sound"}, "sound trigger"),
    ],
)
def test_free_play_needs_an_explicit_architecture_and_never_uses_sound(payload, message):
    with pytest.raises(ValueError, match=message):
        policy.selection(payload, "free_play", Path("sealed"))


def test_other_tester_actions_keep_the_sound_route():
    assert policy.selection({}, "setup", None) == "sound"
    with pytest.raises(ValueError, match="free play"):
        policy.selection({"trigger_policy": "ops_led"}, "setup", Path("sealed"))


@pytest.mark.parametrize(
    "payload,package",
    [
        ({}, Path("sealed")),
        ({"trigger_policy": "sound"}, Path("sealed")),
        ({"trigger_policy": "common_hardware"}, Path("sealed")),
        ({"trigger_policy": "independent"}, None),
        ({"trigger_policy": "hybrid", "capture_mode": "sound"}, Path("sealed")),
        ({"trigger_policy": "sound", "capture_mode": "native_internal"}, Path("sealed")),
        ({"trigger_policy": "invented"}, Path("sealed")),
        ({"trigger_policy": None}, Path("sealed")),
        ({"capture_mode": []}, Path("sealed")),
    ],
)
def test_unavailable_or_conflicting_selection_refuses_before_jobs(tmp_path, payload, package):
    jobs = Jobs()
    client = eligible_app(
        sessions_root=tmp_path, manager=jobs, internal_sync_clock_package=package
    ).test_client()
    response = client.post(
        "/api/tester/run",
        json={
            "action": "free_play",
            "tester_id": "20260922-name",
            "arm_id": "arm5",
            "environment": "indoors",
            "resolution": "arm5",
            **payload,
        },
    )
    assert response.status_code == 400, response.json
    assert not jobs.calls
    assert not list(tmp_path.rglob("trigger-policy.json"))


@pytest.mark.parametrize("selected", ["independent", "ops_led", "hybrid", "iwr_led"])
def test_policy_reaches_launch_and_run_snapshot(tmp_path, selected):
    setup_saw_ball(tmp_path)
    package = tmp_path / "sealed"
    package.mkdir()
    (package / "MANIFEST.json").write_text("{}")
    jobs = Jobs()
    client = eligible_app(
        sessions_root=tmp_path,
        manager=jobs,
        internal_sync_clock_package=package,
        lens_settings_path=tmp_path / "lens.json",
    ).test_client()
    response = client.post(
        "/api/tester/run",
        json={
            "action": "free_play",
            "tester_id": "20260922-name",
            "arm_id": "arm5",
            "environment": "indoors",
            "resolution": "arm5",
            "trigger_policy": selected,
        },
    )
    assert response.status_code == 202, response.json
    command = jobs.calls[0][1][0]
    run = Path(command[command.index("--log-dir") + 1])
    assert "--pi-trace" in command
    assert json.loads((run / "trigger-policy.json").read_text()) == policy.snapshot(selected)
    if selected != "sound":
        assert command[command.index("--tester-trigger-policy") + 1] == selected
        assert command[command.index("--trigger") + 1] == "hardware"
        control = Path(command[command.index("--tester-native-sync-control") + 1])
        assert json.loads((control / "trigger-policy.json").read_text()) == policy.snapshot(
            selected
        )
    else:
        assert "--tester-native-sync-package" not in command


ARCHITECTURES = ["ops_led", "iwr_led", "independent", "hybrid", "common_hardware"]


@pytest.mark.parametrize("package", [None, Path("sealed")])
def test_free_play_offers_the_five_architectures_and_explains_each_block(tmp_path, package):
    client = eligible_app(
        sessions_root=tmp_path, manager=Jobs(), internal_sync_clock_package=package
    ).test_client()
    routes = client.get("/api/tester/free-play/native-sync").json["trigger_policies"]
    assert [row["id"] for row in routes] == ARCHITECTURES
    assert [row["label"].split(" (")[0] for row in routes] == [
        "OPS master",
        "IWR master",
        "Independent triggers",
        "Hybrid",
        "Common hardware trigger",
    ]
    available = [row["id"] for row in routes if row["available"]]
    assert available == ([] if package is None else ["ops_led", "iwr_led", "independent", "hybrid"])
    for row in routes:
        assert row["available"] or row["reason"].startswith("Unavailable")
    if package is not None:
        reason = next(r["reason"] for r in routes if r["id"] == "iwr_led")
        assert "not timing-qualified" in reason.lower()


@pytest.mark.parametrize("selected", ["independent", "ops_led", "hybrid"])
@pytest.mark.parametrize("paired", [False, True])
def test_actual_acquisition_policy_differs_without_delaying_retention(tmp_path, selected, paired):
    owner = object.__new__(NativeFusionExtension)
    owner.policy = policy.snapshot(selected)
    owner.publishing, owner.sequence = False, 0
    owner.collector, owner.camera = object(), object()
    owner.files = SimpleNamespace(
        folder=tmp_path,
        command=lambda: "next",
        publish=lambda *_a, **_k: None,
        cancelled=lambda: False,
    )
    retained = []

    def acquire(_collector, _files, folder, **_kwargs):
        actual, result, _ = bundle(tmp_path)
        assert actual == folder
        raw = {"sample_time": 1.0, "trigger_time": 1.0256, "I": [2048] * 4096, "Q": [2048] * 4096}
        (folder / "ops-camera/ops-response.raw").write_text(
            "\n".join(json.dumps({key: value}) for key, value in raw.items())
        )
        (folder / "iwr-dump.bin").write_bytes(b"own raw IWR")
        result.update(
            ops_capture_observed=True,
            iwr_shot_id=1,
            iwr_raw={"transfer_timing": {"cli_ready_ns": 500, "request_written_ns": 100}},
        )
        if not paired:
            result["status"] = "partial"
        retained.append(folder)
        return result

    owner.tools = SimpleNamespace(attempt=acquire)
    capture = owner.acquire(None, RollingBufferProcessor(), threading.Event(), None)
    folder = retained[0]
    assert (folder / "ops-camera/ops-response.raw").is_file()
    assert (capture is not None) == (paired or selected != "hybrid")
    outcome = json.loads((folder / "policy-decision.json").read_text())
    assert outcome["confirmed"] == paired
    assert outcome["settings"]["policy"] == selected
    assert not outcome["physical_timing_qualified"]
    assert not outcome["settings"]["iwr_initiated_by_ops"]


def test_iwr_only_is_an_independent_candidate_but_passive_in_ops_led():
    decisions = {
        name: policy.decision(policy.snapshot(name), ops=False, iwr=True, association={})
        for name in ("independent", "ops_led")
    }
    assert decisions["independent"]["candidate_opened"]
    assert not decisions["ops_led"]["candidate_opened"]
    assert all(not d["publication_allowed"] for d in decisions.values())


def test_launch_cannot_change_a_retained_run_policy(tmp_path):
    (tmp_path / "trigger-policy.json").write_text(json.dumps(policy.snapshot("hybrid")))
    with pytest.raises(ValueError, match="differs from launch"):
        NativeFusionExtension(tmp_path, tmp_path, policy="independent")


def test_the_staged_clock_package_beside_the_checkout_is_found(tmp_path):
    from openflight.camera import tester_server  # pylint: disable=import-outside-toplevel

    source = tmp_path / "source"
    source.mkdir()
    assert tester_server.staged_clock_package(source) is None
    (tmp_path / "clock-package").mkdir()
    assert tester_server.staged_clock_package(source) is None
    (tmp_path / "clock-package" / "MANIFEST.json").write_text("{}")
    assert tester_server.staged_clock_package(source) == tmp_path / "clock-package"


def test_iwr_master_carries_its_own_ops_split_and_other_routes_keep_theirs():
    assert policy.snapshot("iwr_led")["ops_pre_trigger_segments"] == 16
    assert policy.snapshot("iwr_led", 12)["ops_pre_trigger_segments"] == 12
    assert policy.snapshot("iwr_led")["ops_initiated_by_iwr"] is True
    for name in ("ops_led", "independent", "hybrid"):
        assert policy.snapshot(name)["ops_pre_trigger_segments"] == 6
        assert policy.snapshot(name)["ops_initiated_by_iwr"] is False
        with pytest.raises(ValueError, match="split"):
            policy.snapshot(name, 12)
    for bad in (-1, 33, 12.5, True, "12"):
        with pytest.raises(ValueError, match="split"):
            policy.snapshot("iwr_led", bad)


def test_iwr_master_publishes_an_ops_capture_it_initiated():
    outcome = policy.decision(policy.snapshot("iwr_led"), ops=True, iwr=True, association={})
    assert outcome["publication_allowed"]
    assert outcome["iwr_role"] == "initiator"
    assert not policy.decision(policy.snapshot("iwr_led"), ops=False, iwr=True, association={})[
        "publication_allowed"
    ]


def test_the_chosen_iwr_master_split_reaches_the_kiosk_and_the_run(tmp_path):
    setup_saw_ball(tmp_path)
    package = tmp_path / "sealed"
    package.mkdir()
    (package / "MANIFEST.json").write_text("{}")
    jobs = Jobs()
    client = eligible_app(
        sessions_root=tmp_path,
        manager=jobs,
        internal_sync_clock_package=package,
        lens_settings_path=tmp_path / "lens.json",
    ).test_client()
    payload = {
        "action": "free_play",
        "tester_id": "20260922-name",
        "arm_id": "arm5",
        "environment": "indoors",
        "resolution": "arm5",
        "trigger_policy": "iwr_led",
    }
    refused = client.post("/api/tester/run", json={**payload, "ops_pre_trigger_segments": 40})
    assert refused.status_code == 400 and not jobs.calls
    response = client.post("/api/tester/run", json={**payload, "ops_pre_trigger_segments": 12})
    assert response.status_code == 202, response.json
    command = jobs.calls[0][1][0]
    assert command[command.index("--tester-ops-pre-trigger-segments") + 1] == "12"
    run = Path(command[command.index("--log-dir") + 1])
    assert json.loads((run / "trigger-policy.json").read_text()) == policy.snapshot("iwr_led", 12)

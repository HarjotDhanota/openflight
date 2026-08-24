from silhouette_poc.studio.api import create_app


def test_committed_fixture_is_the_instant_landing_session():
    app = create_app(testing=True)
    response = app.test_client().get("/api/studio/session")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "committed_fixture"
    assert payload["landing"]["ambient_verdict"] == "YES"
    assert payload["landing"]["criteria"][0]["role"] == "primary"
    assert payload["landing"]["criteria"][0]["candidate"] == "ambient_500us"
    assert {
        "solve_rate",
        "median_mm",
        "p90_mm",
        "signed_horizontal_median_mm",
        "signed_horizontal_p90_mm",
        "signed_vertical_median_mm",
        "signed_vertical_p90_mm",
        "rejections",
    } <= set(payload["landing"]["criteria"][0])
    shot = payload["shots"][0]
    assert len(shot["frames"]) == 10
    assert {"silhouette", "template", "track", "extrapolation"} <= set(
        shot["frames"][0]["overlays"]
    )
    assert {"estimated", "truth"} <= set(shot["clubface"]["impact"])


def test_run_endpoint_validates_controls_before_generation():
    app = create_app(testing=True)
    client = app.test_client()

    response = client.post(
        "/api/studio/run",
        json={
            "club": "poc_driver",
            "n": 500,
            "candidate": "ambient_500us",
            "template_variation": "calibrated",
            "radar_residual_mm": 0,
            "sync_mode": "iq_33us",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "n must be between 1 and 32"

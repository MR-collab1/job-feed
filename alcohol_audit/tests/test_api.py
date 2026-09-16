"""API tests. These need the source workbooks, so they skip without them."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(real_dataset):
    from alcohol_audit import api

    api.get_dataset.cache_clear()
    api.get_dataset()
    with TestClient(api.app) as test_client:
        yield test_client
    api.get_dataset.cache_clear()


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["attendances"] > 0


@pytest.mark.parametrize(
    "path,key",
    [
        ("/api/overview", "overview"),
        ("/api/insights/treatment", "treatment"),
        ("/api/insights/ciwa/documentation", "ciwa_documentation"),
        ("/api/insights/ciwa/scores", "ciwa_scores"),
        ("/api/insights/ciwa/repeats", "ciwa_repeats"),
        ("/api/insights/outcomes", "outcomes"),
        ("/api/insights/time-periods", "time_periods"),
        ("/api/insights/data-quality", "data_quality"),
        ("/api/report", "report"),
    ],
)
def test_insight_endpoints_return_their_section(client, path, key):
    response = client.get(path)
    assert response.status_code == 200
    assert key in response.json()


def test_score_detail_endpoint(client):
    response = client.get("/api/insights/ciwa/scores/18")
    assert response.status_code == 200
    detail = response.json()["score_detail"]
    assert detail["score"] == 18
    assert detail["treated"]["count"] <= detail["attendances"]


def test_score_out_of_range_is_rejected(client):
    assert client.get("/api/insights/ciwa/scores/99").status_code == 422


def test_date_filter_narrows_the_cohort(client):
    everything = client.get("/api/overview").json()["attendances_in_scope"]
    window = client.get(
        "/api/overview", params={"date_from": "2026-01-01", "date_to": "2026-01-31"}
    )
    assert window.status_code == 200
    narrowed = window.json()
    assert narrowed["attendances_in_scope"] < everything
    assert narrowed["overview"]["period"]["start"] >= "2026-01-01"
    assert narrowed["overview"]["period"]["end"] <= "2026-01-31"


def test_filters_matching_nothing_return_404(client):
    response = client.get(
        "/api/overview", params={"date_from": "2020-01-01", "date_to": "2020-01-02"}
    )
    assert response.status_code == 404


def test_attendances_are_pseudonymised_by_default(client):
    response = client.get("/api/attendances", params={"limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["pseudonymised"] is True
    assert all(row["patient"].startswith("P-") for row in body["results"])


def test_identifiers_are_refused_unless_explicitly_enabled(client, monkeypatch):
    monkeypatch.delenv("ALCOHOL_AUDIT_ALLOW_IDENTIFIERS", raising=False)
    response = client.get("/api/attendances", params={"include_identifiers": True})
    assert response.status_code == 403


def test_identifiers_can_be_enabled_deliberately(client, monkeypatch):
    monkeypatch.setenv("ALCOHOL_AUDIT_ALLOW_IDENTIFIERS", "1")
    response = client.get(
        "/api/attendances", params={"include_identifiers": True, "limit": 1}
    )
    assert response.status_code == 200
    assert response.json()["pseudonymised"] is False


def test_pagination(client):
    first = client.get("/api/attendances", params={"limit": 2, "offset": 0}).json()
    second = client.get("/api/attendances", params={"limit": 2, "offset": 2}).json()
    assert len(first["results"]) == 2
    assert first["results"][0]["patient"] != second["results"][0]["patient"]


@pytest.mark.skipif(
    os.environ.get("ALCOHOL_AUDIT_SKIP_DECK") == "1", reason="deck build disabled"
)
def test_pptx_endpoint_returns_a_presentation(client):
    response = client.get("/api/report.pptx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml"
    )
    assert response.content[:2] == b"PK"  # a .pptx is a zip archive
    assert len(response.content) > 50_000

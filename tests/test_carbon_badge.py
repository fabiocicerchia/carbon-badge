"""Unit tests for carbon_badge."""

import http.server
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

import carbon_badge
from carbon_badge import badge_color, endpoint_json, format_grams, grams_co2e, main, runner_power_w


def test_conversion_math():
    """1000 minutes * 12.5W runner at 480 g/kWh ≈ 100 g."""
    assert round(grams_co2e(1000), 0) == 100


def test_runner_power_by_type():
    """runner_power_w maps OS labels to their published power draw."""
    assert runner_power_w(["ubuntu-latest"]) == 12.5
    assert runner_power_w(["windows-latest"]) == 30.0
    assert runner_power_w(["macos-latest"]) == 65.0
    assert runner_power_w(["ubuntu-latest-4-cores"]) == 25.0  # 2x the 2-core baseline
    assert runner_power_w(["some-custom-label"]) == 12.5  # unknown -> ubuntu baseline


def test_color_thresholds():
    """badge_color maps gCO2e figures onto the right Shields colors."""
    assert badge_color(50) == "brightgreen"
    assert badge_color(1500) == "yellow"
    assert badge_color(50000) == "red"


def test_format_switches_units():
    """format_grams switches from g to kg above 1000 gCO2e."""
    assert format_grams(900) == "900 gCO2e/mo"
    assert format_grams(2300) == "2.3 kgCO2e/mo"


def test_endpoint_schema():
    """endpoint_json emits the Shields.io endpoint fields."""
    data = endpoint_json(120)
    assert data["schemaVersion"] == 1 and data["label"] == "CI carbon"


def test_cli_offline(capsys):
    """main runs offline with --minutes and prints valid badge JSON."""
    assert main(["owner/repo", "--minutes", "1000"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["message"].endswith("gCO2e/mo")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_gitlab_kwh_skips_self_hosted(monkeypatch):
    """gitlab_kwh_last_30d sums shared-runner job duration, skips self-hosted."""
    now = datetime.now(timezone.utc).isoformat()
    jobs_page1 = [
        {"created_at": now, "duration": 3600, "runner": {"is_shared": True}},
        {"created_at": now, "duration": 3600, "runner": {"is_shared": False}},
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(jobs_page1 if params["page"] == 1 else [])

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    kwh = carbon_badge.gitlab_kwh_last_30d("group/project", token=None)
    assert round(kwh, 6) == round(1 * carbon_badge.DEFAULT_RUNNER_POWER_W / 1000, 6)


def test_live_grid_intensity_injected_client():
    """live_grid_intensity reads carbonIntensity via the injected `get` callable."""

    def fake_get(url, params=None, headers=None, timeout=None):
        assert params["zone"] == "SE"
        return _FakeResponse({"carbonIntensity": 42.5})

    assert carbon_badge.live_grid_intensity("SE", get=fake_get) == 42.5


def test_serve_badge_endpoint_caches_and_404s():
    """badge_handler serves /badge.json from cache within ttl and 404s elsewhere."""
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"schemaVersion": 1, "label": "CI carbon", "message": "1 gCO2e/mo", "color": "brightgreen"}

    httpd = http.server.HTTPServer(("127.0.0.1", 0), carbon_badge.badge_handler(compute, ttl=300))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/badge.json", timeout=5).read()
        assert json.loads(body)["label"] == "CI carbon"
        urllib.request.urlopen(f"http://127.0.0.1:{port}/badge.json", timeout=5).read()
        assert calls["n"] == 1  # second hit within ttl served from cache
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
    finally:
        httpd.shutdown()
        thread.join(timeout=5)

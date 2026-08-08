"""Unit tests for carbon_badge."""

import http.server
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

import carbon_badge
from carbon_badge import (
    badge_color,
    endpoint_json,
    format_grams,
    grams_co2e,
    runner_power_w,
)


def test_conversion_math():
    """1000 minutes at the 9.4 W baseline and 480 g/kWh ~= 75 g.

    9.4 W is 8.18 W machine draw (Eco-CI's curve for GitHub's 4-core EPYC 7763)
    times a 1.15 PUE. It used to be 12.5 W, which was ~1.5x the measured
    full-load figure and described a 2-vCPU machine GitHub retired in 2023."""
    assert round(grams_co2e(1000), 0) == 75


def test_offline_estimate_honours_declared_runner_watts():
    """--minutes used to ignore --runner-watts and always price at 12.5 W, so
    an offline estimate for a 180 W fleet came back 14x low."""
    scaled = grams_co2e(1000) * (180 / carbon_badge.DEFAULT_RUNNER_POWER_W)
    assert round(grams_co2e(1000, watts=180.0), 0) == round(scaled, 0)


def test_runner_power_by_type():
    """runner_power_w maps OS labels to their published power draw."""
    assert runner_power_w(["ubuntu-latest"]) == 9.4
    # Same Azure hardware as Linux — GitHub's 2x Windows billing is a price
    # multiplier, not a power one.
    assert runner_power_w(["windows-latest"]) == 9.4
    # Mac mini M1, whole machine. Apple silicon, nowhere near the old 65 W.
    assert runner_power_w(["macos-latest"]) == 17.9
    # 4 cores *is* the baseline now, so no scaling applies.
    assert runner_power_w(["ubuntu-latest-4-cores"]) == 9.4
    # Unknown is None, not a silent 12.5: the caller has to be able to tell a
    # priced runner from a guessed one so it can warn.
    assert runner_power_w(["some-custom-label"]) is None


def test_arm_and_gpu_are_priced_before_their_os():
    """"ubuntu-22.04-arm" contains "ubuntu", so ordering decides this: a
    generic-first table would charge every ARM runner the full x86 rate."""
    assert runner_power_w(["ubuntu-22.04-arm"]) == 5.6
    assert runner_power_w(["ubuntu-latest-4-cores-gpu"]) == 149.5
    assert runner_power_w(["ubuntu-latest"]) == 9.4


def test_gpu_is_not_core_scaled_but_arm_is():
    """The accelerator is fixed and dominates, so scaling it by CPU cores would
    double-count it. ARM's figure is a CPU baseline, so it does scale."""
    assert runner_power_w(["ubuntu-latest-16-cores-gpu"]) == 149.5
    assert runner_power_w(["ubuntu-24.04-arm-8-cores"]) == 5.6 * 2  # 8 of a 4-core baseline


def test_arm_and_gpu_defaults_are_overridable():
    """They're defaults, not constants — an A100 box isn't a T4 box."""
    overrides = carbon_badge.parse_runner_watts(["gpu=400", "arm=6"])
    assert runner_power_w(["ubuntu-latest-gpu"], overrides) == 400.0
    assert runner_power_w(["ubuntu-22.04-arm"], overrides) == 6.0


def test_core_count_parsed_from_every_common_label_shape():
    """Only "-N-cores" was matched before, so "linux-x64-16core" — a real
    larger-runner name — was priced as a 2-core box, an 8x undercount."""
    assert runner_power_w(["ubuntu-latest-16-cores"]) == 9.4 * 4
    assert runner_power_w(["ubuntu-latest-16-core"]) == 9.4 * 4
    assert runner_power_w(["ubuntu-24.04-8vcpu"]) == 9.4 * 2


def test_bare_number_prices_every_runner():
    """The low-effort case: one value, set once in the workflow. It beats the
    built-in guess table — the developer knows their runners, the API doesn't."""
    overrides = carbon_badge.parse_runner_watts(["180"])
    assert overrides == {carbon_badge.ANY_RUNNER: 180.0}
    assert runner_power_w(["self-hosted", "linux"], overrides) == 180.0
    assert runner_power_w(["my-builder"], overrides) == 180.0
    assert runner_power_w(["ubuntu-latest"], overrides) == 180.0


def test_specific_label_still_beats_the_blanket_figure():
    """Mixed fleets: the blanket value is the fallback, not the override."""
    overrides = carbon_badge.parse_runner_watts(["180", "gpu=320"])
    assert runner_power_w(["ubuntu-latest-4-cores-gpu"], overrides) == 320.0
    assert runner_power_w(["ubuntu-latest"], overrides) == 180.0


def test_runner_watts_overrides_price_custom_and_self_hosted_labels():
    """The only way to price a runner whose specs the API never exposes."""
    overrides = carbon_badge.parse_runner_watts(["my-builder=180", "gpu=320"])
    assert runner_power_w(["my-builder"], overrides) == 180.0
    # Substring match, so one entry covers a family.
    assert runner_power_w(["ubuntu-latest-4-cores-gpu"], overrides) == 320.0
    # An exact label beats the built-in table, so a hosted label can be corrected.
    assert runner_power_w(["ubuntu-latest"], {"ubuntu-latest": 9.0}) == 9.0


@pytest.mark.parametrize(
    "bad", ["no-equals", "=100", "label=", "label=abc", "label=0", "label=-5"]
)
def test_parse_runner_watts_rejects_malformed_input(bad):
    """A typo'd override that silently did nothing would leave the badge wrong
    in exactly the case the user was trying to fix."""
    with pytest.raises(ValueError):
        carbon_badge.parse_runner_watts([bad])


def test_unknown_runners_are_charged_not_skipped(monkeypatch, capsys):
    """Skipping self-hosted scored it as zero, so moving a build onto the
    biggest machine you own improved the badge. It's charged and reported now."""
    now = datetime.now(timezone.utc).isoformat()
    runs = [{"id": 1}]
    jobs = [
        {
            "labels": ["self-hosted", "linux", "x64"],
            "started_at": now,
            "completed_at": now,
        }
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        if "/jobs" in url:
            return _FakeResponse({"jobs": jobs})
        return _FakeResponse({"workflow_runs": runs if params["page"] == 1 else []})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    carbon_badge.ci_kwh_last_30d("owner/repo", token=None)
    err = capsys.readouterr().err
    assert "self-hosted" in err and "--runner-watts" in err


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


def _usage(kwh, measured_jobs=0, total_jobs=0, measured_kwh=0.0, guessed_kwh=0.0):
    return carbon_badge.CiUsage(kwh, measured_jobs, total_jobs, measured_kwh, guessed_kwh)


def test_confidence_is_driven_by_energy_not_job_count():
    """One long job on an unknown runner undermines the total more than a dozen
    short known ones, so the score weighs energy."""
    # 9 tidy known jobs, 1 huge unmeasured one holding most of the energy.
    many_small = _usage(kwh=10.0, measured_jobs=9, total_jobs=10, guessed_kwh=8.0)
    assert carbon_badge.confidence(many_small) == "rough"
    # Same job ratio, but the unknown runner barely contributes.
    same_ratio = _usage(
        kwh=10.0, measured_jobs=9, total_jobs=10, measured_kwh=9.9, guessed_kwh=0.1
    )
    assert carbon_badge.confidence(same_ratio) == "measured"


def test_declaring_an_unknown_runner_clears_the_guess(monkeypatch):
    """The escape route from "rough": nine short recognised jobs plus one
    ten-hour unrecognised one puts ~97% of the energy on a fallback wattage.
    Declaring that runner removes the guess entirely — and, here, corrects a
    figure that was 14x low, which is the real reason the warning matters."""
    runs = [{"id": 1}]
    jobs = [_job(["ubuntu-latest"], 2)] * 9 + [_job(["my-builder"], 600)]

    def fake_get(url, params=None, headers=None, timeout=None):
        if "/jobs" in url:
            return _FakeResponse({"jobs": jobs})
        if "/artifacts" in url:
            return _FakeResponse({"artifacts": []})
        return _FakeResponse({"workflow_runs": runs if params["page"] == 1 else []})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)

    blind = carbon_badge.ci_kwh_last_30d("o/r", token=None)
    assert blind.guessed_kwh / blind.kwh > 0.9
    assert carbon_badge.confidence(blind) == "rough"

    declared = carbon_badge.ci_kwh_last_30d(
        "o/r", token=None, runner_watts=carbon_badge.parse_runner_watts(["my-builder=180"])
    )
    assert declared.guessed_kwh == 0.0
    # Believed, not verified — so "estimated", never "measured".
    assert carbon_badge.confidence(declared) == "estimated"
    assert declared.kwh > blind.kwh * 10


def test_confidence_levels():
    assert carbon_badge.confidence(_usage(0.0)) == "no CI"
    # Nothing self-reported, but every runner was recognised: durations are
    # real, only the wattages are modelled.
    assert carbon_badge.confidence(_usage(10.0)) == "estimated"
    assert carbon_badge.confidence(_usage(10.0, measured_kwh=5.0)) == "partial"
    assert carbon_badge.confidence(_usage(10.0, measured_kwh=10.0)) == "measured"
    # A quarter of the energy on a fallback wattage outranks everything else.
    assert carbon_badge.confidence(_usage(10.0, measured_kwh=7.0, guessed_kwh=3.0)) == "rough"


def test_badge_states_its_own_confidence():
    """"113 gCO2e/mo" from instrumented jobs and the same string from a wattage
    table are different claims; the badge must not render them identically."""
    assert endpoint_json(120, _usage(1.0, 24, 24, measured_kwh=1.0))["message"] == (
        "120 gCO2e/mo"  # fully measured earns an unqualified figure
    )
    assert endpoint_json(120, _usage(1.0, 18, 24, measured_kwh=0.5))["message"] == (
        "120 gCO2e/mo (~18/24 measured)"
    )
    assert endpoint_json(120, _usage(1.0))["message"] == "120 gCO2e/mo (estimated)"
    assert endpoint_json(120, _usage(1.0, guessed_kwh=0.9))["message"] == (
        "120 gCO2e/mo (rough)"
    )
    # No provenance available at all (--minutes): stay silent rather than imply.
    assert endpoint_json(120)["message"] == "120 gCO2e/mo"


def test_badge_grams_always_cover_all_ci():
    """The colour and the number reflect *all* CI, measured or inferred — a
    badge that shrank while instrumentation lagged would reward stalling."""
    partly = endpoint_json(1500, _usage(1.0, 5, 50, measured_kwh=0.1))
    fully = endpoint_json(1500, _usage(1.0, 50, 50, measured_kwh=1.0))
    assert partly["color"] == fully["color"] == "yellow"
    assert partly["message"].startswith("1.5 kgCO2e/mo")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_parse_carbon_artifact():
    """The artifact name is the wire format, so parsing it is load-bearing."""
    assert carbon_badge.parse_carbon_artifact("carbon.v1.142.2.7168.build") == (
        142.0,
        2,
        7168,
    )
    # A repo's ordinary artifacts share the listing and must be ignored.
    assert carbon_badge.parse_carbon_artifact("gitleaks-results.sarif") is None
    assert carbon_badge.parse_carbon_artifact("github-pages") is None
    # A future format must not be silently misread as v1.
    assert carbon_badge.parse_carbon_artifact("carbon.v2.142.2.7168.build") is None
    assert carbon_badge.parse_carbon_artifact("") is None


def test_watts_from_specs_reproduces_the_known_runner():
    """Anchored on Eco-CI's curve for the machine GitHub actually uses: a
    4-vCPU / 16 GiB runner at 8.18 W machine draw x 1.15 PUE ~= 9.4 W.

    Previously fitted to "2 vCPU / 7 GB = 12.5 W", wrong twice over — the spec
    was retired in 2023 and the wattage was ~1.5x the measured figure — which
    priced a real 4-vCPU runner at 24.3 W, about 3x too high."""
    assert round(carbon_badge.watts_from_specs(4, 16384), 1) == 9.4
    # Scales off real hardware rather than a label.
    assert carbon_badge.watts_from_specs(64, 262144) > 100


def _artifact(name, run_id, created=None, expired=False):
    return {
        "name": name,
        "workflow_run": {"id": run_id},
        "created_at": created or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expired": expired,
    }


def _serve(artifacts, runs, calls):
    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        page = params["page"] if params else 1
        if "/artifacts" in url:
            return _FakeResponse({"artifacts": artifacts if page == 1 else []})
        if "/jobs" in url:
            run_id = int(url.split("/runs/")[1].split("/jobs")[0])
            return _FakeResponse(
                {"jobs": [_job(["ubuntu-latest"], 60)]} if run_id else {"jobs": []}
            )
        return _FakeResponse({"workflow_runs": runs if page == 1 else []})

    return fake_get


def test_artifact_kwh_by_run_keys_by_run_and_downloads_nothing(monkeypatch):
    """Keyed by run because instrumentation lands one workflow at a time, so
    the answer is almost never all-or-nothing."""
    calls = []
    artifacts = [
        _artifact("carbon.v1.3600.2.7168.build", 11),
        _artifact("carbon.v1.1800.2.7168.test", 11),  # same run, second job
        _artifact("carbon.v1.3600.2.7168.lint", 22),
        _artifact("some-build-output.zip", 33),  # not ours; ignored
    ]
    monkeypatch.setattr(carbon_badge.requests, "get", _serve(artifacts, [], calls))
    by_run, jobs = carbon_badge.artifact_kwh_by_run("o/r", token=None)

    assert jobs == 3
    assert set(by_run) == {11, 22}
    watts = carbon_badge.watts_from_specs(2, 7168)
    assert round(by_run[11], 9) == round(1.5 * watts / 1000, 9)  # both jobs summed
    assert not any("/jobs" in u or u.endswith("/zip") for u in calls)


def test_partial_instrumentation_tops_up_from_the_api(monkeypatch):
    """The realistic state. Self-reported runs are used as-is; only the rest
    cost a request. Accurate throughout, and cheaper with every workflow you
    instrument — rather than all-or-nothing at some threshold."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [{"id": i, "run_started_at": now, "updated_at": now} for i in (1, 2, 3, 4)]
    artifacts = [_artifact("carbon.v1.3600.2.7168.a", 1), _artifact("carbon.v1.3600.2.7168.b", 2)]
    calls = []
    monkeypatch.setattr(carbon_badge.requests, "get", _serve(artifacts, runs, calls))

    usage = carbon_badge.ci_kwh_last_30d("o/r", token=None)
    kwh = usage.kwh

    # Only the two uninstrumented runs were queried.
    assert sum(1 for u in calls if "/jobs" in u) == 2
    # ...and the badge can state the ratio exactly: 2 markers, 2 jobs seen live.
    assert (usage.measured_jobs, usage.total_jobs) == (2, 4)
    self_reported = 2 * carbon_badge.watts_from_specs(2, 7168) / 1000  # 2 x 1h
    # 2 x a 1h ubuntu job (_job takes minutes). Read from the table rather than
    # hardcoded, so recalibrating the wattages does not silently rot this test.
    from_api = 2 * 1.0 * carbon_badge.RUNNER_POWER_W["ubuntu"] / 1000
    assert round(kwh, 9) == round(self_reported + from_api, 9)


def test_full_coverage_costs_no_per_run_calls(monkeypatch):
    """Fully instrumented: zero per-run requests."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [{"id": i, "run_started_at": now, "updated_at": now} for i in (1, 2, 3)]
    artifacts = [_artifact(f"carbon.v1.3600.2.7168.j{i}", i) for i in (1, 2, 3)]
    calls = []
    monkeypatch.setattr(carbon_badge.requests, "get", _serve(artifacts, runs, calls))

    usage = carbon_badge.ci_kwh_last_30d("o/r", token=None)
    assert not any("/jobs" in u for u in calls)
    assert round(usage.kwh, 9) == round(
        3 * carbon_badge.watts_from_specs(2, 7168) / 1000, 9
    )
    assert (usage.measured_jobs, usage.total_jobs) == (3, 3)


def test_ignore_self_reported_does_not_consult_artifacts(monkeypatch):
    """--ignore-self-reported must not touch the artifacts listing at all,
    otherwise the two paths cannot be reconciled against each other."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [{"id": 1, "run_started_at": now, "updated_at": now}]
    calls = []
    monkeypatch.setattr(
        carbon_badge.requests, "get",
        _serve([_artifact("carbon.v1.3600.2.7168.a", 1)], runs, calls),
    )
    carbon_badge.ci_kwh_last_30d("o/r", token=None, use_artifacts=False)
    assert not any("/artifacts" in u for u in calls)
    assert sum(1 for u in calls if "/jobs" in u) == 1


def test_expired_and_out_of_window_markers_are_ignored(monkeypatch):
    """Retention outlives the window, so old markers must not inflate a month."""
    now = datetime.now(timezone.utc)
    fresh = now.isoformat().replace("+00:00", "Z")
    old = (now - timedelta(days=40)).isoformat().replace("+00:00", "Z")
    artifacts = [
        _artifact("carbon.v1.3600.2.7168.a", 1, fresh),
        _artifact("carbon.v1.3600.2.7168.b", 2, old),
        _artifact("carbon.v1.3600.2.7168.c", 3, fresh, expired=True),
    ]
    monkeypatch.setattr(carbon_badge.requests, "get", _serve(artifacts, [], []))
    by_run, jobs = carbon_badge.artifact_kwh_by_run("o/r", token=None)
    assert set(by_run) == {1} and jobs == 1


def test_declared_watts_still_beat_the_model(monkeypatch):
    """Someone who knows their hardware's real draw beats a linear model."""
    monkeypatch.setattr(
        carbon_badge.requests, "get",
        _serve([_artifact("carbon.v1.3600.2.7168.a", 1)], [], []),
    )
    by_run, _ = carbon_badge.artifact_kwh_by_run(
        "o/r", token=None, runner_watts={carbon_badge.ANY_RUNNER: 200.0}
    )
    assert round(by_run[1], 9) == round(200.0 / 1000, 9)


def _fake_github(runs, jobs_by_run):
    """Serve the runs list and each run's jobs, counting the calls made."""
    calls = {"jobs": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        if "/jobs" in url:
            calls["jobs"] += 1
            run_id = int(url.split("/runs/")[1].split("/jobs")[0])
            return _FakeResponse({"jobs": jobs_by_run[run_id]})
        page = params["page"]
        return _FakeResponse({"workflow_runs": runs if page == 1 else []})

    return fake_get, calls


def _run(run_id, minutes, workflow_id=1):
    """A run whose wall-clock spans `minutes`."""
    start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=minutes)
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "run_started_at": start.isoformat().replace("+00:00", "Z"),
        "updated_at": end.isoformat().replace("+00:00", "Z"),
    }


def _job(labels, minutes):
    start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=minutes)
    return {
        "labels": labels,
        "started_at": start.isoformat().replace("+00:00", "Z"),
        "completed_at": end.isoformat().replace("+00:00", "Z"),
    }


def test_gitlab_kwh_charges_self_managed_runners(monkeypatch):
    """Self-managed runners used to be skipped — scored as zero emissions, the
    same perverse incentive as the GitHub path. Both jobs are charged now."""
    now = datetime.now(timezone.utc).isoformat()
    jobs_page1 = [
        {"created_at": now, "duration": 3600, "runner": {"is_shared": True}},
        {"created_at": now, "duration": 3600, "runner": {"is_shared": False}},
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(jobs_page1 if params["page"] == 1 else [])

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    kwh = carbon_badge.gitlab_kwh_last_30d("group/project", token=None)
    assert round(kwh, 6) == round(2 * carbon_badge.DEFAULT_RUNNER_POWER_W / 1000, 6)


def test_gitlab_runner_watts_matches_on_tags(monkeypatch):
    """Tags and the runner description are GitLab's equivalent of a label."""
    now = datetime.now(timezone.utc).isoformat()
    jobs_page1 = [
        {
            "created_at": now,
            "duration": 3600,
            "tag_list": ["big-metal"],
            "runner": {"is_shared": False},
        }
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(jobs_page1 if params["page"] == 1 else [])

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    kwh = carbon_badge.gitlab_kwh_last_30d(
        "group/project", token=None, runner_watts={"big-metal": 200.0}
    )
    assert round(kwh, 6) == round(200.0 / 1000, 6)


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
        return {
            "schemaVersion": 1,
            "label": "CI carbon",
            "message": "1 gCO2e/mo",
            "color": "brightgreen",
        }

    httpd = http.server.HTTPServer(("127.0.0.1", 0), carbon_badge.badge_handler(compute, ttl=300))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{port}/badge.json"
        body = urllib.request.urlopen(url, timeout=5).read()
        assert json.loads(body)["label"] == "CI carbon"
        urllib.request.urlopen(url, timeout=5).read()
        assert calls["n"] == 1  # second hit within ttl served from cache
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
    finally:
        httpd.shutdown()
        thread.join(timeout=5)

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
    """ "ubuntu-22.04-arm" contains "ubuntu", so ordering decides this: a
    generic-first table would charge every ARM runner the full x86 rate."""
    assert runner_power_w(["ubuntu-22.04-arm"]) == 5.6
    assert runner_power_w(["ubuntu-latest-4-cores-gpu"]) == 89.9
    assert runner_power_w(["ubuntu-latest"]) == 9.4


def test_gpu_is_not_core_scaled_but_arm_is():
    """The accelerator is fixed and dominates, so scaling it by CPU cores would
    double-count it. ARM's figure is a CPU baseline, so it does scale."""
    assert runner_power_w(["ubuntu-latest-16-cores-gpu"]) == 89.9
    assert runner_power_w(["ubuntu-24.04-arm-8-cores"]) == 10.0  # affine, not 2x5.6


def test_arm_and_gpu_defaults_are_overridable():
    """They're defaults, not constants — an A100 box isn't a T4 box."""
    overrides = carbon_badge.parse_runner_watts(["gpu=400", "arm=6"])
    assert runner_power_w(["ubuntu-latest-gpu"], overrides) == 400.0
    assert runner_power_w(["ubuntu-22.04-arm"], overrides) == 6.0


def test_core_count_parsed_from_every_common_label_shape():
    """Only "-N-cores" was matched before, so "linux-x64-16core" — a real
    larger-runner name — was priced as a 2-core box, an 8x undercount."""
    # Affine: a fixed base plus a per-core term, so 16 cores is not 4x a
    # 4-core machine. Eco-CI measures 1.76 W at idle — the base does not scale.
    assert runner_power_w(["ubuntu-latest-16-cores"]) == 34.0
    assert runner_power_w(["ubuntu-latest-16-core"]) == 34.0
    assert runner_power_w(["ubuntu-24.04-8vcpu"]) == 17.6


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


@pytest.mark.parametrize("bad", ["no-equals", "=100", "label=", "label=abc", "label=0", "label=-5"])
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


def test_badge_passes_no_verdict():
    """One colour whatever the figure. A red-amber-green scale graded project
    size while implying virtue, and on the fleet it was built for every repo
    sat in the bottom band anyway — a constant dressed as a signal."""
    for grams in (1, 50, 1500, 50000, 10_000_000):
        assert endpoint_json(grams)["color"] == carbon_badge.BADGE_COLOR


def test_format_switches_units():
    """format_grams switches from g to kg above 1000 gCO2e."""
    assert format_grams(900) == "900 gCO2e/mo"
    assert format_grams(2300) == "2.3 kgCO2e/mo"


def test_endpoint_schema():
    """endpoint_json emits the Shields.io endpoint fields."""
    data = endpoint_json(120)
    assert data["schemaVersion"] == 1 and data["label"] == "CI carbon"


def _usage(kwh, measured_jobs=0, total_jobs=0, measured_kwh=0.0, guessed_kwh=0.0):
    grams = kwh * carbon_badge.DEFAULT_GRID_INTENSITY
    return carbon_badge.CiUsage(kwh, grams, measured_jobs, total_jobs, measured_kwh, guessed_kwh)


def test_pagination_cap_is_reported_not_silent(monkeypatch, capsys):
    """A cap that silently drops runs understates the badge and reads as a
    quiet month. Same reasoning as the closed-PR search cap."""
    full_page = [{"id": i, "run_started_at": None, "updated_at": None} for i in range(100)]

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse({"workflow_runs": full_page, "total_count": 9999})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    runs = carbon_badge._list_runs("o/r", None, "https://api.github.com", "2026-07-09")
    err = capsys.readouterr().err
    assert len(runs) == 100 * carbon_badge._MAX_PAGES
    assert "undercount" in err and "9999" in err


def test_no_warning_when_everything_was_read(monkeypatch, capsys):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse({"workflow_runs": [{"id": 1}], "total_count": 1})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    carbon_badge._list_runs("o/r", None, "https://api.github.com", "2026-07-09")
    assert "undercount" not in capsys.readouterr().err


def test_confidence_is_driven_by_energy_not_job_count():
    """One long job on an unknown runner undermines the total more than a dozen
    short known ones, so the score weighs energy."""
    # 9 tidy known jobs, 1 huge unmeasured one holding most of the energy.
    many_small = _usage(kwh=10.0, measured_jobs=9, total_jobs=10, guessed_kwh=8.0)
    assert carbon_badge.confidence(many_small) == "rough"
    # Same job ratio, but the unknown runner barely contributes.
    same_ratio = _usage(kwh=10.0, measured_jobs=9, total_jobs=10, measured_kwh=9.9, guessed_kwh=0.1)
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
    """ "113 gCO2e/mo" from instrumented jobs and the same string from a wattage
    table are different claims; the badge must not render them identically."""
    assert endpoint_json(120, _usage(1.0, 24, 24, measured_kwh=1.0))["message"] == (
        "120 gCO2e/mo"  # fully measured earns an unqualified figure
    )
    assert endpoint_json(120, _usage(1.0, 18, 24, measured_kwh=0.5))["message"] == (
        "120 gCO2e/mo (~18/24 measured)"
    )
    assert endpoint_json(120, _usage(1.0))["message"] == "120 gCO2e/mo (estimated)"
    assert endpoint_json(120, _usage(1.0, guessed_kwh=0.9))["message"] == ("120 gCO2e/mo (rough)")
    # No provenance available at all (--minutes): stay silent rather than imply.
    assert endpoint_json(120)["message"] == "120 gCO2e/mo"


def test_badge_grams_always_cover_all_ci():
    """The colour and the number reflect *all* CI, measured or inferred — a
    badge that shrank while instrumentation lagged would reward stalling."""
    partly = endpoint_json(1500, _usage(1.0, 5, 50, measured_kwh=0.1))
    fully = endpoint_json(1500, _usage(1.0, 50, 50, measured_kwh=1.0))
    # Same grams either way — only the provenance marker differs.
    assert partly["message"].startswith("1.5 kgCO2e/mo")
    assert fully["message"].startswith("1.5 kgCO2e/mo")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_parse_carbon_artifact():
    """The artifact name is the wire format, so parsing it is load-bearing."""
    assert carbon_badge.parse_carbon_artifact(
        "carbon.v1.142.4.16384.ubuntu.eastus.build-a1b2c3d4"
    ) == (142.0, 4, 16384, "ubuntu", "eastus")
    # The platform is load-bearing: the same specs draw very different power on
    # Apple silicon, so a name without one must not be accepted as v1.
    assert carbon_badge.parse_carbon_artifact("carbon.v1.142.4.16384.build") is None
    # A name without a region is v1-shaped but incomplete, and must not parse.
    assert carbon_badge.parse_carbon_artifact("carbon.v1.142.4.16384.ubuntu.build") is None
    # A repo's ordinary artifacts share the listing and must be ignored.
    assert carbon_badge.parse_carbon_artifact("gitleaks-results.sarif") is None
    assert carbon_badge.parse_carbon_artifact("github-pages") is None
    # A future format must not be silently misread as v1.
    assert carbon_badge.parse_carbon_artifact("carbon.v2.142.2.7168.build") is None
    assert carbon_badge.parse_carbon_artifact("") is None


def test_the_two_pricing_paths_agree_at_every_size():
    """The same machine must cost the same whether it was recognised by its
    label or reported its own hardware — otherwise a repo's figure moves as it
    instruments, with nothing in the world having changed.

    They used to agree only at the 4-vCPU calibration point, because the label
    path scaled the table value proportionally while the model is affine. The
    gap reached 12% by 64 cores. Both now go through one law.
    """
    for cores in (4, 8, 16, 32, 64):
        by_label = runner_power_w([f"ubuntu-latest-{cores}-cores"])
        by_specs = carbon_badge.watts_from_specs(cores, cores * carbon_badge.MEM_PER_VCPU_MB)
        assert by_label == by_specs, f"{cores} cores: {by_label} vs {by_specs}"


def test_every_platform_reproduces_its_table_entry():
    """Per-core draw is derived from each table entry rather than hardcoded, so
    the model and the table cannot drift apart when a figure is revised."""
    for platform in ("ubuntu", "windows", "arm", "macos"):
        assert (
            carbon_badge.watts_from_specs(4, 16384, platform)
            == carbon_badge.RUNNER_POWER_W[platform]
        )


def test_watts_from_specs_is_platform_aware():
    """The linear model is fitted to x86 shared VMs and does not transfer.
    A Mac mini M1 reporting 3 vCPU / 7 GiB comes out at 6.8 W through it against
    a measured 15.53 W — so a platform-blind self-reported path would have been
    2.6x *worse* than the label lookup it replaces."""
    assert carbon_badge.watts_from_specs(3, 7168, "macos") == carbon_badge.RUNNER_POWER_W["macos"]
    # ARM scales its own baseline rather than borrowing the x86 one.
    assert carbon_badge.watts_from_specs(4, 16384, "arm") == carbon_badge.RUNNER_POWER_W["arm"]
    # Doubling the cores at the standard memory ratio, affine not proportional.
    assert carbon_badge.watts_from_specs(8, 32768, "arm") == 10.0
    # x86 still uses the linear model, and defaults to it.
    assert carbon_badge.watts_from_specs(4, 16384, "ubuntu") == carbon_badge.watts_from_specs(
        4, 16384
    )


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


def _serve(artifacts, runs, calls, jobs_per_run=1):
    """`jobs_per_run` is what the API reports a run really had — the
    denominator the partial-run check compares marker counts against."""

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        page = params["page"] if params else 1
        if "/artifacts" in url:
            return _FakeResponse({"artifacts": artifacts if page == 1 else []})
        if "/jobs" in url:
            return _FakeResponse(
                {"jobs": [_job(["ubuntu-latest"], 60)] * jobs_per_run}
                if page == 1
                else {"jobs": []}
            )
        return _FakeResponse({"workflow_runs": runs if page == 1 else []})

    return fake_get


def test_artifact_kwh_by_run_keys_by_run_and_downloads_nothing(monkeypatch):
    """Keyed by run because instrumentation lands one workflow at a time, so
    the answer is almost never all-or-nothing."""
    calls = []
    artifacts = [
        _artifact("carbon.v1.3600.2.7168.ubuntu.eastus.build", 11),
        _artifact("carbon.v1.1800.2.7168.ubuntu.eastus.test", 11),  # same run, second job
        _artifact("carbon.v1.3600.2.7168.ubuntu.eastus.lint", 22),
        _artifact("some-build-output.zip", 33),  # not ours; ignored
    ]
    monkeypatch.setattr(carbon_badge.requests, "get", _serve(artifacts, [], calls))
    by_run, jobs = carbon_badge.artifact_kwh_by_run("o/r", token=None)

    assert jobs == 3
    assert set(by_run) == {11, 22}
    watts = carbon_badge.watts_from_specs(2, 7168)
    kwh, _grams, count = by_run[11]
    assert round(kwh, 9) == round(1.5 * watts / 1000, 9)  # both jobs summed
    assert count == 2  # and the denominator the partial-run check needs
    assert not any("/jobs" in u or u.endswith("/zip") for u in calls)


def test_partial_instrumentation_tops_up_from_the_api(monkeypatch):
    """The realistic state. Self-reported runs are used as-is; only the rest
    cost a request. Accurate throughout, and cheaper with every workflow you
    instrument — rather than all-or-nothing at some threshold."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [
        {"id": i, "workflow_id": 7, "run_started_at": now, "updated_at": now} for i in (1, 2, 3, 4)
    ]
    artifacts = [
        _artifact("carbon.v1.3600.2.7168.ubuntu.eastus.a", 1),
        _artifact("carbon.v1.3600.2.7168.ubuntu.eastus.b", 2),
    ]
    calls = []
    monkeypatch.setattr(carbon_badge.requests, "get", _serve(artifacts, runs, calls))

    usage = carbon_badge.ci_kwh_last_30d("o/r", token=None)
    kwh = usage.kwh

    # Two uninstrumented runs queried, plus one sampled to learn how many jobs
    # a run of this workflow really has.
    assert sum(1 for u in calls if "/jobs" in u) == 3
    # ...and the badge can state the ratio exactly: 2 markers, 2 jobs seen live.
    assert (usage.measured_jobs, usage.total_jobs) == (2, 4)
    self_reported = 2 * carbon_badge.watts_from_specs(2, 7168) / 1000  # 2 x 1h
    # 2 x a 1h ubuntu job (_job takes minutes). Read from the table rather than
    # hardcoded, so recalibrating the wattages does not silently rot this test.
    from_api = 2 * 1.0 * carbon_badge.RUNNER_POWER_W["ubuntu"] / 1000
    assert round(kwh, 9) == round(self_reported + from_api, 9)


def test_full_coverage_costs_one_sample_per_workflow(monkeypatch):
    """Fully instrumented: one sampled run per workflow to establish the job
    count, and nothing else."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [
        {"id": i, "workflow_id": 7, "run_started_at": now, "updated_at": now} for i in (1, 2, 3)
    ]
    artifacts = [_artifact(f"carbon.v1.3600.2.7168.ubuntu.eastus.j{i}", i) for i in (1, 2, 3)]
    calls = []
    monkeypatch.setattr(carbon_badge.requests, "get", _serve(artifacts, runs, calls))

    usage = carbon_badge.ci_kwh_last_30d("o/r", token=None)
    assert sum(1 for u in calls if "/jobs" in u) == 1  # one workflow, one sample
    assert round(usage.kwh, 9) == round(3 * carbon_badge.watts_from_specs(2, 7168) / 1000, 9)
    assert (usage.measured_jobs, usage.total_jobs) == (3, 3)


def test_ignore_self_reported_does_not_consult_artifacts(monkeypatch):
    """--ignore-self-reported must not touch the artifacts listing at all,
    otherwise the two paths cannot be reconciled against each other."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [{"id": 1, "workflow_id": 7, "run_started_at": now, "updated_at": now}]
    calls = []
    monkeypatch.setattr(
        carbon_badge.requests,
        "get",
        _serve([_artifact("carbon.v1.3600.2.7168.ubuntu.eastus.a", 1)], runs, calls),
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
        _artifact("carbon.v1.3600.2.7168.ubuntu.eastus.a", 1, fresh),
        _artifact("carbon.v1.3600.2.7168.ubuntu.eastus.b", 2, old),
        _artifact("carbon.v1.3600.2.7168.ubuntu.eastus.c", 3, fresh, expired=True),
    ]
    monkeypatch.setattr(carbon_badge.requests, "get", _serve(artifacts, [], []))
    by_run, jobs = carbon_badge.artifact_kwh_by_run("o/r", token=None)
    assert set(by_run) == {1} and jobs == 1


def test_declared_watts_still_beat_the_model(monkeypatch):
    """Someone who knows their hardware's real draw beats a linear model."""
    monkeypatch.setattr(
        carbon_badge.requests,
        "get",
        _serve([_artifact("carbon.v1.3600.2.7168.ubuntu.eastus.a", 1)], [], []),
    )
    by_run, _ = carbon_badge.artifact_kwh_by_run(
        "o/r", token=None, runner_watts={carbon_badge.ANY_RUNNER: 200.0}
    )
    assert round(by_run[1][0], 9) == round(200.0 / 1000, 9)


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
    usage = carbon_badge.gitlab_kwh_last_30d("group/project", token=None)
    assert round(usage.kwh, 6) == round(2 * carbon_badge.DEFAULT_RUNNER_POWER_W / 1000, 6)
    # GitLab gets a confidence marker too: nothing self-reports there, but both
    # jobs were priced at the fallback, so the badge must not read as measured.
    assert carbon_badge.confidence(usage) == "rough"


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
    usage = carbon_badge.gitlab_kwh_last_30d(
        "group/project", token=None, runner_watts={"big-metal": 200.0}
    )
    assert round(usage.kwh, 6) == round(200.0 / 1000, 6)
    # Declared, so nothing is guessed — "estimated", not "rough".
    assert carbon_badge.confidence(usage) == "estimated"


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


def test_a_partly_instrumented_run_does_not_lose_its_other_jobs(monkeypatch):
    """The reviewer's case, and the incremental-adoption path this code exists
    to serve: one run, two jobs, only the short one instrumented.

    Trusting the marker alone charged 1 hour and silently dropped the 10-hour
    sibling — an 11x undercount that still reported confidence "measured",
    because a marker proves *a* job reported, not that all of them did.
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [{"id": 1, "workflow_id": 7, "run_started_at": now, "updated_at": now}]
    # The API says this run had two jobs; only one wrote a marker.
    jobs = [_job(["ubuntu-latest"], 60), _job(["ubuntu-latest"], 600)]
    marker = _artifact("carbon.v1.3600.4.16384.ubuntu.eastus.short", 1)

    def fake_get(url, params=None, headers=None, timeout=None):
        page = params["page"] if params else 1
        if "/artifacts" in url:
            return _FakeResponse({"artifacts": [marker] if page == 1 else []})
        if "/jobs" in url:
            return _FakeResponse({"jobs": jobs if page == 1 else []})
        return _FakeResponse({"workflow_runs": runs if page == 1 else []})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    usage = carbon_badge.ci_kwh_last_30d("o/r", token=None)

    # Priced from the API for the whole run: 1 h + 10 h of ubuntu-latest.
    expected = 11 * carbon_badge.RUNNER_POWER_W["ubuntu"] / 1000
    assert round(usage.kwh, 9) == round(expected, 9)
    # And it must not claim to be measured on the strength of one marker.
    assert usage.measured_jobs == 0
    assert carbon_badge.confidence(usage) != "measured"


def test_markers_are_not_double_counted_when_a_run_is_topped_up(monkeypatch):
    """Topping up must replace the partial markers, not add to them."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [{"id": 1, "workflow_id": 7, "run_started_at": now, "updated_at": now}]
    jobs = [_job(["ubuntu-latest"], 60), _job(["ubuntu-latest"], 60)]

    def fake_get(url, params=None, headers=None, timeout=None):
        page = params["page"] if params else 1
        if "/artifacts" in url:
            return _FakeResponse(
                {"artifacts": [_artifact("carbon.v1.3600.4.16384.ubuntu.eastus.a", 1)]}
                if page == 1
                else {"artifacts": []}
            )
        if "/jobs" in url:
            return _FakeResponse({"jobs": jobs if page == 1 else []})
        return _FakeResponse({"workflow_runs": runs if page == 1 else []})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    usage = carbon_badge.ci_kwh_last_30d("o/r", token=None)
    # Exactly the API figure for two 1-hour jobs — the marker adds nothing.
    assert round(usage.kwh, 9) == round(2 * carbon_badge.RUNNER_POWER_W["ubuntu"] / 1000, 9)


def test_run_jobs_pages_past_thirty(monkeypatch):
    """The API defaults to 30 per page, so a wider matrix was truncated — and
    the confidence ratio is now derived from these counts."""
    pages = {1: [_job(["ubuntu-latest"], 1)] * 100, 2: [_job(["ubuntu-latest"], 1)] * 5}

    def fake_get(url, params=None, headers=None, timeout=None):
        assert params["per_page"] == 100
        return _FakeResponse({"jobs": pages.get(params["page"], [])})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    assert len(carbon_badge.run_jobs(1, "o/r", token=None)) == 105


def test_undeclared_warning_never_suggests_a_command_that_will_not_parse(capsys):
    """The message exists to hand over the fix; a broken flag is worse than an
    honest "cannot suggest one"."""
    carbon_badge._warn_undeclared_runners({"(no labels)": 3, "my-builder,linux": 2})
    err = capsys.readouterr().err
    for line in err.strip().splitlines():
        if "--runner-watts '" not in line:
            continue
        flag = line.split("--runner-watts '")[1].split("'")[0]
        carbon_badge.parse_runner_watts([flag.replace("<watts>", "180")])


def test_gitlab_honours_a_blanket_override_on_untagged_jobs(monkeypatch):
    """Untagged is the common GitLab case; an `if tags` guard skipped the
    blanket figure entirely and priced a 180 W fleet at the 9.4 W baseline."""
    now = datetime.now(timezone.utc).isoformat()
    jobs_page1 = [{"created_at": now, "duration": 3600, "runner": {"is_shared": True}}]

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(jobs_page1 if params["page"] == 1 else [])

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    usage = carbon_badge.gitlab_kwh_last_30d(
        "group/project", token=None, runner_watts={carbon_badge.ANY_RUNNER: 180.0}
    )
    assert round(usage.kwh, 6) == round(180.0 / 1000, 6)


def test_an_unusually_small_newest_run_cannot_lower_the_bar(monkeypatch):
    """The denominator is one number per workflow applied across a whole month,
    and the sample is the newest instrumented run. If that run happened to be
    small — conditional jobs that did not fire, a narrower matrix — the bar
    dropped and genuinely partial runs passed, reviving the undercount.

    A marker cannot outnumber its run's jobs, so the highest marker count seen
    for the workflow is a free lower bound and the larger of the two wins.
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    # Newest run had a single job. An older one ran three, fully instrumented.
    # The middle one ran three and reported two — the case that must not pass.
    runs = [
        {"id": 1, "workflow_id": 7, "run_started_at": now, "updated_at": now},
        {"id": 2, "workflow_id": 7, "run_started_at": now, "updated_at": now},
        {"id": 3, "workflow_id": 7, "run_started_at": now, "updated_at": now},
    ]
    jobs_by_run = {1: 1, 2: 3, 3: 3}
    markers = (
        [_artifact("carbon.v1.3600.4.16384.ubuntu.eastus.solo", 1)]
        + [_artifact(f"carbon.v1.3600.4.16384.ubuntu.eastus.a{i}", 2) for i in range(3)]
        + [_artifact(f"carbon.v1.3600.4.16384.ubuntu.eastus.b{i}", 3) for i in range(2)]
    )
    queried = []

    def fake_get(url, params=None, headers=None, timeout=None):
        page = params["page"] if params else 1
        if "/artifacts" in url:
            return _FakeResponse({"artifacts": markers if page == 1 else []})
        if "/jobs" in url:
            rid = int(url.split("/runs/")[1].split("/jobs")[0])
            queried.append(rid)
            n = jobs_by_run[rid] if page == 1 else 0
            return _FakeResponse({"jobs": [_job(["ubuntu-latest"], 60)] * n})
        return _FakeResponse({"workflow_runs": runs if page == 1 else []})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    usage = carbon_badge.ci_kwh_last_30d("o/r", token=None)

    # Sampling run 1 alone would give a bar of 1, passing run 3 on two markers.
    # Run 3 must instead be priced from the API.
    assert 3 in queried, "the partly reported run was trusted on a low bar"
    # Seven jobs really ran (1 + 3 + 3), each counted exactly once.
    assert usage.total_jobs == 7
    assert round(usage.kwh, 9) == round(7 * carbon_badge.RUNNER_POWER_W["ubuntu"] / 1000, 9)


def test_skipped_jobs_do_not_make_completeness_unreachable(monkeypatch):
    """Real shape, from greenlint's release workflow: one job runs, two are
    skipped by their `if:`. GitHub reports all three with both timestamps set.

    Counting the skipped ones gave a denominator of 3 against a maximum of 1
    obtainable marker, so the run could never be trusted — a fully
    instrumented workflow still paying an API call every run, forever. That is
    the failure mode where the whole feature stops earning its overhead.
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [{"id": 1, "workflow_id": 9, "run_started_at": now, "updated_at": now}]
    api_jobs = [
        dict(_job(["ubuntu-latest"], 8), name="release-please", conclusion="success"),
        dict(_job(["ubuntu-latest"], 0), name="build", conclusion="skipped"),
        dict(_job(["ubuntu-latest"], 0), name="pypi", conclusion="skipped"),
    ]
    markers = [_artifact("carbon.v1.480.4.16384.ubuntu.eastus.release-please", 1)]
    queried = []

    def fake_get(url, params=None, headers=None, timeout=None):
        page = params["page"] if params else 1
        if "/artifacts" in url:
            return _FakeResponse({"artifacts": markers if page == 1 else []})
        if "/jobs" in url:
            queried.append(url)
            return _FakeResponse({"jobs": api_jobs if page == 1 else []})
        return _FakeResponse({"workflow_runs": runs if page == 1 else []})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    usage = carbon_badge.ci_kwh_last_30d("o/r", token=None)

    # One marker for one job that ran: complete.
    assert usage.measured_jobs == 1
    assert usage.total_jobs == 1, "skipped jobs inflated the ratio"
    assert carbon_badge.confidence(usage) == "measured"
    # Only the denominator sample — the run itself was not re-queried.
    assert len(queried) == 1


def test_skipped_jobs_contribute_no_energy(monkeypatch):
    """They also carry timestamps, sometimes with completed_at *before*
    started_at, so they must be dropped rather than clamped to zero."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    runs = [{"id": 1, "workflow_id": 9, "run_started_at": now, "updated_at": now}]
    api_jobs = [
        dict(_job(["ubuntu-latest"], 60), name="real", conclusion="success"),
        {
            "name": "skipped",
            "labels": ["ubuntu-latest"],
            "conclusion": "skipped",
            "started_at": "2026-08-01T00:00:02Z",
            "completed_at": "2026-08-01T00:00:01Z",
        },
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        page = params["page"] if params else 1
        if "/artifacts" in url:
            return _FakeResponse({"artifacts": []})
        if "/jobs" in url:
            return _FakeResponse({"jobs": api_jobs if page == 1 else []})
        return _FakeResponse({"workflow_runs": runs if page == 1 else []})

    monkeypatch.setattr(carbon_badge.requests, "get", fake_get)
    usage = carbon_badge.ci_kwh_last_30d("o/r", token=None)
    assert usage.total_jobs == 1
    assert round(usage.kwh, 9) == round(carbon_badge.RUNNER_POWER_W["ubuntu"] / 1000, 9)


def test_grid_factor_averages_a_day_not_an_instant():
    """A single reading is a poor multiplier for a 30-day total. Grids swing
    2-3x daily and the refresh runs on a fixed cron — 02:17 Monday for the
    fleet — so `latest` would price a month at an overnight low and the figure
    would move week to week on the clock alone."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse({"history": [{"carbonIntensity": v} for v in (100, 200, 300, 400)]})

    got = carbon_badge.live_grid_intensity("SE", get=fake_get)
    assert got == 250.0
    assert any("history" in c for c in calls)
    assert not any(c.endswith("latest") for c in calls)


def test_grid_factor_falls_back_to_the_instant_reading(capsys):
    """History coverage varies by zone and plan; a token that can only reach
    `latest` must still work, loudly."""

    def fake_get(url, params=None, headers=None, timeout=None):
        if "history" in url:
            return _FakeResponse({"history": []})
        return _FakeResponse({"carbonIntensity": 412.0})

    assert carbon_badge.live_grid_intensity("SE", get=fake_get) == 412.0
    assert "instantaneous" in capsys.readouterr().err


def test_grid_factor_ignores_gaps_in_the_history():
    """Electricity Maps returns nulls for hours it has no data for."""

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(
            {
                "history": [
                    {"carbonIntensity": 100},
                    {"carbonIntensity": None},
                    {"carbonIntensity": 300},
                ]
            }
        )

    assert carbon_badge.live_grid_intensity("SE", get=fake_get) == 200.0


# --- live grid providers ----------------------------------------------------


def _resp(payload):
    return _FakeResponse(payload)


def test_energy_charts_takes_the_latest_non_null_reading():
    """15-minute series, and the newest slot is often still null."""

    def fake_get(url, params=None, headers=None, timeout=None):
        assert "energy-charts" in url and params["country"] == "de"
        return _resp({"co2eq": [400.0, 420.0, 411.5, None]})

    assert carbon_badge._energy_charts_factor("de", get=fake_get) == 411.5


def test_uk_falls_back_to_forecast_within_the_current_half_hour():
    """NESO publishes `actual` only once a settlement period closes."""

    def fake_get(url, params=None, headers=None, timeout=None):
        return _resp({"data": [{"intensity": {"forecast": 127, "actual": None}}]})

    assert carbon_badge._uk_factor(get=fake_get) == 127.0

    def settled(url, params=None, headers=None, timeout=None):
        return _resp({"data": [{"intensity": {"forecast": 127, "actual": 133}}]})

    assert carbon_badge._uk_factor(get=settled) == 133.0


def test_eia_weights_the_fuel_mix_of_the_newest_hour_only():
    """EIA gives generation by fuel, not intensity, so the number is ours:
    generation-weighted IPCC lifecycle factors. Mixing two hours would blend a
    partially reported one into a complete one."""
    rows = [
        {"period": "2026-08-08T18", "fueltype": "COL", "value": "100"},
        {"period": "2026-08-08T18", "fueltype": "WND", "value": "100"},
        # An older hour that must not be blended in.
        {"period": "2026-08-08T17", "fueltype": "COL", "value": "10000"},
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        assert params["facets[respondent][]"] == "PJM"
        return _resp({"response": {"data": rows}})

    got = carbon_badge._eia_factor("PJM", "key", get=fake_get)
    expected = (100 * 820.0 + 100 * 11.0) / 200
    assert round(got, 6) == round(expected, 6)


def test_eia_ignores_negative_generation():
    """Net-negative rows are storage charging, not a fuel."""
    rows = [
        {"period": "p", "fueltype": "NG", "value": "100"},
        {"period": "p", "fueltype": "OTH", "value": "-50"},
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        return _resp({"response": {"data": rows}})

    assert carbon_badge._eia_factor("PJM", "key", get=fake_get) == 490.0


def test_us_regions_need_a_key_and_degrade_without_one():
    """No key means no US provider — the annual average, not a crash."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return _resp({})

    assert carbon_badge.live_region_factor("northcentralus", get=fake_get) is None
    assert calls == []


def test_a_failing_provider_never_breaks_the_refresh(capsys):
    """A grid lookup is an optional refinement; it must not fail a badge."""

    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("provider down")

    assert carbon_badge.live_region_factor("uksouth", get=boom) is None
    assert "live grid lookup failed" in capsys.readouterr().err


def test_resolver_caches_per_region_and_honours_an_explicit_figure():
    """One request per distinct region per refresh, not one per job — and a
    declared figure skips the providers entirely."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return _resp({"data": [{"intensity": {"actual": 100}}]})

    resolve = carbon_badge.region_factor_resolver(get=fake_get)
    assert resolve("uksouth") == 100.0
    assert resolve("uksouth") == 100.0
    assert len(calls) == 1, "resolver should memoise"

    declared = carbon_badge.region_factor_resolver(override=333.0, get=fake_get)
    assert declared("uksouth") == 333.0
    assert len(calls) == 1, "an explicit figure must not consult a provider"


def test_unknown_region_falls_back_to_the_annual_average():
    resolve = carbon_badge.region_factor_resolver()
    assert resolve("moonbase1") == carbon_badge.DEFAULT_GRID_INTENSITY


class TestLoadFactor:
    """--load-factor: what to do about a flat wattage on an I/O-bound job."""

    def setup_method(self):
        carbon_badge.LOAD_FACTOR = 1.0

    def teardown_method(self):
        carbon_badge.LOAD_FACTOR = 1.0

    def test_default_is_unchanged_behaviour(self):
        # The API exposes no utilisation, so full load stays the default: the
        # honest reading of "we do not know" for a number that should not
        # flatter the caller.
        assert carbon_badge.watts_from_specs(4, 16 * 1024) == 9.4
        assert carbon_badge.runner_power_w(["ubuntu-latest"]) == 9.4

    def test_only_the_variable_part_scales(self):
        # A machine at 0% CPU still draws its idle power, so a load factor of
        # zero is not zero watts. A plain multiply would have understated by
        # about the margin the default overstates.
        carbon_badge.LOAD_FACTOR = 0.0
        idle = carbon_badge.watts_from_specs(4, 16 * 1024)
        assert idle == pytest.approx(9.4 * carbon_badge.IDLE_FRACTION, abs=0.01)
        assert idle > 0

    def test_a_quarter_load_is_not_a_quarter_of_the_power(self):
        carbon_badge.LOAD_FACTOR = 0.25
        watts = carbon_badge.watts_from_specs(4, 16 * 1024)
        assert watts == pytest.approx(3.87, abs=0.01)
        assert watts > 9.4 * 0.25  # idle floor keeps it above the naive figure

    def test_both_routes_agree_under_a_load_factor(self):
        # The invariant the whole power model is built around: the label path
        # and the self-reported path must price the same machine identically,
        # or a repo's figure moves as it instruments.
        carbon_badge.LOAD_FACTOR = 0.4
        assert carbon_badge.runner_power_w(["ubuntu-latest"]) == carbon_badge.watts_from_specs(
            carbon_badge.BASELINE_VCPU, carbon_badge.BASELINE_MEM_GB * 1024
        )

    def test_macos_scales_too(self):
        carbon_badge.LOAD_FACTOR = 0.5
        assert (
            carbon_badge.watts_from_specs(8, 16 * 1024, "macos")
            < carbon_badge.RUNNER_POWER_W["macos"]
        )

    def test_declared_runner_watts_scale_as_well(self):
        # A declared figure is a full-load figure like any other, so it must
        # scale — otherwise --runner-watts and --load-factor would contradict.
        carbon_badge.LOAD_FACTOR = 0.5
        got = carbon_badge.runner_power_w(["self-hosted"], {"self-hosted": 100.0})
        assert got == pytest.approx(
            100 * (carbon_badge.IDLE_FRACTION + 0.5 * (1 - carbon_badge.IDLE_FRACTION)), abs=0.01
        )

    def test_out_of_range_is_refused(self):
        with pytest.raises(SystemExit):
            carbon_badge.main(["owner/repo", "--load-factor", "1.5"])
        with pytest.raises(SystemExit):
            carbon_badge.main(["owner/repo", "--load-factor", "-0.1"])


# --- ci-api -----------------------------------------------------------------


def _dt(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _now_z():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ci_snapshot(generated_at="2026-08-08T18:00:00Z"):
    return {
        "generated_at": generated_at,
        "countries": {
            "JP": {
                "basis": "measured",
                "direct": 477,
                "lifecycle": 522,
                "consumption_lifecycle": 567,
            },
            "AE": {"basis": "annual-average", "direct": 468, "lifecycle": 513},
        },
        # Zones carry neither consumption figure: the import adjustment is a
        # national number and does not describe one bidding zone.
        "zones": {
            "US/ERCO": {"basis": "measured", "direct": 380, "lifecycle": 425},
        },
    }


def test_ci_api_reports_consumption_lifecycle_for_a_country():
    got = carbon_badge._ci_api_factor("JP", _ci_snapshot(), now=_dt("2026-08-08T18:10:00Z"))
    # Not 477 (direct) and not 522 (lifecycle): the reported figure carries both
    # the upstream scope and the trade adjustment.
    assert got == 567.0


def test_ci_api_zone_falls_back_to_lifecycle():
    got = carbon_badge._ci_api_factor("US/ERCO", _ci_snapshot(), now=_dt("2026-08-08T18:10:00Z"))
    assert got == 425.0


def test_ci_api_refuses_an_annual_average():
    """The API's fallback for a grid with no live feed is a yearly constant —
    which is what AZURE_REGION_GRID already holds. Taking it would print "live
    at 513" for a figure no more live than the table's."""
    got = carbon_badge._ci_api_factor("AE", _ci_snapshot(), now=_dt("2026-08-08T18:10:00Z"))
    assert got is None


def test_ci_api_refuses_a_missed_pipeline_run():
    """Nothing in the response says it is stale — the objects are static and
    served with no application in the request path — so a missed hourly run
    only shows up as an old generated_at."""
    fresh = _dt("2026-08-08T19:00:00Z")  # exactly 60 minutes on, inside the 65
    assert carbon_badge._ci_api_factor("JP", _ci_snapshot(), now=fresh) == 567.0

    late = _dt("2026-08-08T19:10:00Z")  # 70 minutes on from generated_at
    assert carbon_badge._ci_api_factor("JP", _ci_snapshot(), now=late) is None


def test_ci_api_snapshot_is_fetched_once_for_many_regions():
    """The API allows 1 request per 10s per IP. Resolving several regions with
    a lookup each would 429 on the second; /v1/latest.json is the whole world
    in one object, so N regions stay at one request."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return _resp(_ci_snapshot(generated_at=_now_z()))

    resolve = carbon_badge.region_factor_resolver(get=fake_get)
    assert resolve("japaneast") == 567.0
    assert resolve("japanwest") == 567.0
    assert resolve("southcentralus") == 425.0
    assert len(calls) == 1, calls
    assert calls[0].endswith("/v1/latest.json")


def test_ci_api_failure_leaves_the_annual_average_standing(capsys):
    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("ci-api down")

    resolve = carbon_badge.region_factor_resolver(get=boom)
    assert resolve("japaneast") == carbon_badge.AZURE_REGION_GRID["japaneast"]
    assert "ci-api snapshot failed" in capsys.readouterr().err

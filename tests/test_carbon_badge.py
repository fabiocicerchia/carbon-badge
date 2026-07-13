"""Unit tests for carbon_badge."""

import json

from carbon_badge import badge_color, endpoint_json, format_grams, grams_co2e, main


def test_conversion_math():
    """1000 minutes * 12.5W runner at 480 g/kWh ≈ 100 g."""
    assert round(grams_co2e(1000), 0) == 100


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

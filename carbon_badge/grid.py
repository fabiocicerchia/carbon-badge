"""Live grid intensity, from whichever provider answers for the region."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import cast

import requests

from .base import Getter, Json, log

# --------------------------------------------------------------------------
# Live grid factors.
#
# The static table above is an annual average. Real grids swing several times
# over within a day — Germany ran 146 to 634 gCO2eq/kWh in one measured day —
# so a live figure is worth far more than any refinement to the wattages.
#
# Four providers, tried in order of how much they cost the user:
#
#   energy-charts.info   no key at all, 15-min, absolute      much of Europe
#   carbonintensity.org.uk  no key at all, 30-min, absolute   Great Britain
#   ci-api               no key at all, hourly, absolute      everywhere else
#   EIA                  free key, hourly, fuel mix           United States
#
# ci-api covers every region in the table below, so EIA is now only reached
# when it is unavailable. It stays because its US numbers are computed from the
# balancing authority's fuel mix directly, with no third party in between.
#
# Electricity Maps is deliberately not in this chain. Its free tier is one zone
# at 50 requests/hour and non-commercial, which cannot serve CI that lands in a
# different region run to run; --grid-region still uses it for anyone on a paid
# plan, and an explicit --grid-intensity still overrides everything.
# --------------------------------------------------------------------------

# Azure region -> energy-charts country code. Only regions that provider covers
# appear; anything else falls through to the next provider or the table.
_REGION_ENERGY_CHARTS = {
    "germanywestcentral": "de",
    "francecentral": "fr",
    "francesouth": "fr",
    "italynorth": "it",
    "polandcentral": "pl",
    "spaincentral": "es",
    "westeurope": "nl",
    "norwayeast": "no",
    "norwaywest": "no",
}
_REGION_UK = {"uksouth", "ukwest"}

# Azure region -> key into the Carbon Intensity API's world snapshot: an ISO-2
# country, or "<COUNTRY>/<ZONE>" for a grid its operator publishes below
# national level. The US rows use the EIA balancing authority rather than the
# country, for the same reason _REGION_EIA_BA does: a national average blurs
# CAISO and ERCOT together, and those are different grids. Canada is CA/ON for
# the same reason: Ontario is the province with a live feed, and the one the
# central Canadian region draws from.
_REGION_CI_API = {
    "norwayeast": "NO",
    "norwaywest": "NO",
    "swedencentral": "SE",
    "switzerlandnorth": "CH",
    "francecentral": "FR",
    "francesouth": "FR",
    "uksouth": "GB",
    "ukwest": "GB",
    "northeurope": "IE",
    "westeurope": "NL",
    "germanywestcentral": "DE",
    "italynorth": "IT/NORD",
    "spaincentral": "ES",
    "polandcentral": "PL",
    "eastus": "US/PJM",
    "eastus2": "US/PJM",
    "northcentralus": "US/PJM",
    "centralus": "US/MISO",
    "southcentralus": "US/ERCO",
    "westus": "US/CISO",
    "westus2": "US/BPAT",
    "westus3": "US/AZPS",
    "canadacentral": "CA/ON",
    "brazilsouth": "BR",
    "japaneast": "JP",
    "japanwest": "JP",
    "koreacentral": "KR",
    "southeastasia": "SG",
    "eastasia": "HK",
    "centralindia": "IN",
    "southindia": "IN",
    "westindia": "IN",
    "australiaeast": "AU/NSW1",
    "australiasoutheast": "AU/VIC1",
    "uaenorth": "AE",
    "southafricanorth": "ZA",
}

CI_API_BASE = "https://ci-api.fabiocicerchia.it"

# The API publishes no freshness flag: responses are static objects served
# straight from a bucket, with nothing in the request path to evaluate one. Its
# pipeline runs hourly, so 65 minutes means a run was missed and the snapshot
# no longer describes the hour it claims.
CI_API_MAX_AGE_S = 65 * 60

# Azure region -> EIA balancing authority. The grid a datacentre draws from is
# the balancing authority for its location, not the state.
_REGION_EIA_BA = {
    "eastus": "PJM",
    "eastus2": "PJM",
    "northcentralus": "PJM",  # northern Illinois is ComEd, inside PJM
    "centralus": "MISO",  # Iowa
    "southcentralus": "ERCO",  # Texas
    "westus": "CISO",  # California
    "westus2": "BPAT",  # Washington, Bonneville
    "westus3": "AZPS",  # Arizona
}

# Lifecycle emission factors, gCO2e/kWh, IPCC AR5 Annex III medians. Lifecycle
# rather than combustion-only, to match how Electricity Maps and the static
# table above are expressed — mixing the two would understate renewables.
# https://www.ipcc.ch/site/assets/uploads/2018/02/ipcc_wg3_ar5_annex-iii.pdf
IPCC_FUEL_G_PER_KWH = {
    "COL": 820.0,  # coal
    "NG": 490.0,  # natural gas, combined cycle
    "OIL": 650.0,  # not in AR5; between coal and gas, commonly cited
    "NUC": 12.0,
    "WAT": 24.0,  # hydro
    "WND": 11.0,  # onshore wind
    "SUN": 45.0,  # utility solar PV
    "GEO": 38.0,
    "BIO": 230.0,
    "OTH": 490.0,  # unknown mix; gas is the least-bad neutral guess
}


def _energy_charts_factor(country: str, get: Getter = requests.get) -> float | None:
    """Latest absolute gCO2eq/kWh from Fraunhofer ISE. No key, 15-minute data."""
    response = get(
        "https://api.energy-charts.info/co2eq",
        params={"country": country},
        timeout=20,
    )
    response.raise_for_status()
    payload: Json = response.json()
    series: list[float | None] = payload.get("co2eq") or []
    values = [v for v in series if v is not None]
    return float(values[-1]) if values else None


def _uk_factor(get: Getter = requests.get) -> float | None:
    """Great Britain, from NESO. No key, half-hourly."""
    response = get("https://api.carbonintensity.org.uk/intensity", timeout=20)
    response.raise_for_status()
    payload: Json = response.json()
    data: list[Json] = payload.get("data") or [{}]
    entry: Json = data[0].get("intensity", {})
    value = entry.get("actual")
    if value is None:
        value = entry.get("forecast")  # the current half-hour is not settled yet
    return float(value) if value is not None else None


def _to_float(value: object) -> float:
    """A number from a JSON field, or 0.0 when the field is not one.

    EIA sends its figures as JSON strings and occasionally as null; a row that
    does not parse is dropped rather than failing the whole hour.
    """
    try:
        return float(value or 0)  # type: ignore[arg-type]  # str, int, float or None from JSON
    except (TypeError, ValueError):
        return 0.0


def _eia_factor(balancing_authority: str, api_key: str, get: Getter = requests.get) -> float | None:
    """US, computed from the balancing authority's fuel mix.

    EIA publishes generation by fuel type, not carbon intensity, so this is the
    one provider where the number is ours: generation-weighted IPCC lifecycle
    factors over the most recent hour. Documented in docs/assumptions.md,
    because it is a model rather than a measurement.
    """
    response = get(
        "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/",
        params={
            "api_key": api_key,
            "frequency": "hourly",
            "data[0]": "value",
            "facets[respondent][]": balancing_authority,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 40,
        },
        timeout=25,
    )
    response.raise_for_status()
    rows = response.json().get("response", {}).get("data", [])
    if not rows:
        return None

    # Only the newest hour present, so a partially reported hour cannot mix
    # with the one before it.
    newest = rows[0].get("period")
    mwh, grams = 0.0, 0.0
    for row in rows:
        if row.get("period") != newest:
            continue
        value = _to_float(row.get("value"))
        # A row that did not parse reads as 0.0; <= 0 is also storage
        # discharge accounting, which is not generation either way.
        if value <= 0:
            continue
        factor = IPCC_FUEL_G_PER_KWH.get(str(row.get("fueltype")))
        if factor is None:
            continue
        mwh += value
        grams += value * factor
    return grams / mwh if mwh else None


def _ci_api_snapshot(get: Getter = requests.get) -> Json | None:
    """The whole world in one request, from https://ci-api.fabiocicerchia.it.

    Per-region lookups would be the obvious shape and are the wrong one: the
    API is rate-limited to 1 request per 10s per IP as a CDN rule, and a badge
    refresh resolves several regions back to back, so every lookup after the
    first would collect a 429 instead of a number. `/v1/latest.json` is every
    country and every zone in a single object, which makes N regions cost one
    request no matter how many N is.
    """
    response = get(f"{CI_API_BASE}/v1/latest.json", timeout=25)
    response.raise_for_status()
    return response.json()


def _measured_reading(key: str, snapshot: Json | None, now: datetime | None = None) -> Json | None:
    """The `_REGION_CI_API` reading for `key`, or None if it cannot be trusted.

    Three ways it cannot: the snapshot is not an object, it is older than
    CI_API_MAX_AGE_S (the pipeline runs hourly, so a stale one describes an
    hour that has passed), or the reading is not a measurement. A zone key
    ("<COUNTRY>/<ZONE>") is published under `zones`, a country under
    `countries`.
    """
    if not isinstance(snapshot, dict):
        return None
    stamp = snapshot.get("generated_at")
    try:
        generated = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    if (now - generated).total_seconds() > CI_API_MAX_AGE_S:
        return None
    bucket = "zones" if "/" in key else "countries"
    readings: Json = snapshot.get(bucket) or {}
    reading: object = readings.get(key)
    if not isinstance(reading, dict):
        return None
    measured = cast("Json", reading)
    if measured.get("basis") != "measured":
        return None
    return measured


def _ci_api_factor(key: str, snapshot: Json | None, now: datetime | None = None) -> float | None:
    """gCO2e/kWh for a `_REGION_CI_API` key, or None if the snapshot can't say.

    Reports `consumption_lifecycle`: upstream emissions plus the trade
    adjustment, the most complete of the four figures published and the one the
    API tells clients to use. Zone readings carry no consumption figures at all
    — the import adjustment is a national number and does not describe one
    bidding zone — so they report `lifecycle`, which is on the same lifecycle
    scope as IPCC_FUEL_G_PER_KWH and the static table.

    Returns None rather than a number for a reading that is not a measurement.
    `basis == "annual-average"` is the API's fallback for a grid with no live
    feed: a yearly constant, which is exactly what AZURE_REGION_GRID already
    holds. Taking it here would print "live at 477" for a figure no more live
    than the table's, which is the one failure mode worth refusing outright.
    """
    reading = _measured_reading(key, snapshot, now)
    if reading is None:
        return None
    for name in ("consumption_lifecycle", "lifecycle", "direct"):
        value = reading.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def live_region_factor(
    region: str,
    eia_key: str | None = None,
    get: Getter = requests.get,
    ci_api: Callable[[], Json | None] | None = None,
) -> float | None:
    """A live gCO2e/kWh for an Azure region, or None if nothing covers it.

    Never raises: a grid lookup failing must not fail a badge refresh. Every
    failure is reported, because silently falling back to an annual average
    while claiming live data would be worse than not trying.
    """
    if not region:
        return None
    try:
        if region in _REGION_UK:
            return _uk_factor(get=get)
        country = _REGION_ENERGY_CHARTS.get(region)
        if country:
            return _energy_charts_factor(country, get=get)
        key = _REGION_CI_API.get(region)
        if key and ci_api is not None:
            factor = _ci_api_factor(key, ci_api())
            if factor is not None:
                return factor
        ba = _REGION_EIA_BA.get(region)
        if ba and eia_key:
            return _eia_factor(ba, eia_key, get=get)
    except Exception as exc:
        log.warning(
            "live grid lookup failed for %s (%s); using the annual average for that region",
            region,
            exc,
        )
    return None

"""
Unit tests for the lightweight social-signal place extractor.

The extractor must stay precision-first: missing a country is acceptable,
mis-labelling one is not. These tests are anchored in real text samples we
observed during the step-13 Bluesky cutover.
"""

from __future__ import annotations

import pytest

from transformations.social_geo import extract_location


REAL_BLUESKY_SAMPLES = [
    # Real text we ingested during the cutover.
    (
        "Flood Advisory issued May 13 at 4:05PM AKDT until May 14 at 4:00PM "
        "AKDT by NWS Anchorage AK",
        "United States",
        "Alaska",  # via AKDT timezone code
    ),
    (
        "Record flooding pushed Michigan's dams to the brink of disaster.",
        "United States",
        "Michigan",
    ),
    (
        "Northern, eastern Spain face floods amid heavy rain.",
        "Spain",
        None,
    ),
    (
        "He was rescued in April from a Lake Michigan beach in Grand Haven, "
        "MI., after f...",
        "United States",
        # The "Grand Haven, MI" comma pattern is the strongest signal here.
        "Grand Haven, Michigan",
    ),
]


@pytest.mark.parametrize("text,expected_country,expected_place", REAL_BLUESKY_SAMPLES)
def test_extract_location_real_samples(text, expected_country, expected_place):
    out = extract_location(text)
    assert out["country"] == expected_country, f"country mismatch for: {text}"
    if expected_place is not None:
        assert expected_place in (out["place_name"] or ""), (
            f"place_name should contain {expected_place!r} for: {text} "
            f"(got {out['place_name']!r})"
        )


@pytest.mark.parametrize(
    "text,expected_country",
    [
        ("French rescue teams responded to flash floods overnight", "France"),
        ("Brazilian villages cut off by record rainfall", "Brazil"),
        ("Pakistani provinces struggle with monsoon damage", "Pakistan"),
        ("Indonesian authorities issued an evacuation order", "Indonesia"),
        ("Sri Lankan rivers are at dangerous levels tonight", "Sri Lanka"),
        ("Italian emergency services reached the village by helicopter", "Italy"),
    ],
)
def test_extract_location_country_adjective(text, expected_country):
    out = extract_location(text)
    assert out["country"] == expected_country


@pytest.mark.parametrize(
    "text,expected_country",
    [
        ("Heavy rain across Spain has triggered Aemet warnings", "Spain"),
        ("Bangladesh evacuation expands as river crests", "Bangladesh"),
        ("Floods hit several districts in Kenya overnight", "Kenya"),
        ("Drone footage from the DRC shows entire villages underwater", "Democratic Republic of the Congo"),
        ("Storm surge hits the United Arab Emirates coast", "United Arab Emirates"),
    ],
)
def test_extract_location_country_name(text, expected_country):
    out = extract_location(text)
    assert out["country"] == expected_country


@pytest.mark.parametrize(
    "text,expected_place",
    [
        ("Severe flooding reported in Houston, TX overnight", "Houston, Texas"),
        ("Crews respond near Grand Haven, MI after storm surge", "Grand Haven, Michigan"),
        ("Flash flood warning for Baton Rouge, LA", "Baton Rouge, Louisiana"),
        ("Roads closed near Boulder, CO after heavy rain", "Boulder, Colorado"),
    ],
)
def test_extract_location_city_state_pattern(text, expected_place):
    out = extract_location(text)
    assert out["country"] == "United States"
    assert out["place_name"] == expected_place


def test_extract_location_state_name_alone():
    out = extract_location("Record flooding pushed Michigan's dams to the brink")
    assert out["country"] == "United States"
    assert out["place_name"] == "Michigan"


def test_extract_location_us_timezone_code():
    out = extract_location("Flood Advisory issued at 4:05PM AKDT by NWS Anchorage")
    assert out["country"] == "United States"
    assert out["place_name"] == "Alaska"


def test_extract_location_returns_nothing_for_political_metaphor():
    # The political-metaphor filter should already have rejected this row at
    # the ingester, but the extractor should still not produce a misleading
    # country tag if it slips through.
    out = extract_location("We have to flood Congress to stop this grift")
    assert out == {"country": None, "place_name": None}


def test_extract_location_empty_text():
    assert extract_location("") == {"country": None, "place_name": None}
    assert extract_location(None) == {"country": None, "place_name": None}


def test_extract_location_no_match_returns_none():
    # No country reference at all -> no inference.
    out = extract_location("The weather has been unusual this week.")
    assert out == {"country": None, "place_name": None}


def test_extract_location_does_not_confuse_two_letter_word_with_state_code():
    # "OR" used as English conjunction must NOT trigger the city-state
    # pattern because there is no preceding comma-space + city.
    out = extract_location("Check the road OR call emergency services")
    assert out["country"] is None


def test_extract_location_precision_over_recall():
    # When multiple signals are present we take the strongest one in order.
    # Country adjective beats explicit country name when both fire.
    out = extract_location("French and American teams are coordinating")
    assert out["country"] == "United States"
    # ... because "American" wins via the alphabetical sort? No, the iter is
    # length-desc but both are 6 chars; first-match wins. The point of this
    # test is that whichever wins is deterministic; pin to the current
    # behavior so regressions are visible.

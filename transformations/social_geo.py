"""
Lightweight, dependency-free place extraction for social flood signals.

Most Bluesky posts do not carry coordinates or a structured ``place`` field,
so without text-based extraction every signal lands in
``staging.social_flood_signals`` with ``country = NULL``. That cripples
``marts.flood_events_with_social_signals`` because the country join clause
never matches.

This module fills the gap with five precision-first strategies, evaluated in
order:

1. Country adjective ("French floods" -> France).
2. Explicit country name ("...across Spain..." -> Spain).
3. ``"<City>, <ST>"`` pattern with a known US state code -> United States.
4. Full US state name anywhere in the text -> United States.
5. US timezone abbreviation (e.g. ``AKDT``, ``EDT``) -> United States.

The goal is precision, not recall. A signal that we cannot confidently place
keeps ``country = None`` rather than getting a misleading label. The whole
module is small (one regex pass per strategy), pure-Python, and has no
external dependencies.
"""

from __future__ import annotations

import re
from typing import Final


# Each pattern is a pre-compiled regex; ordered by curation priority. The
# value of the dict is the canonical country name we want stored in
# ``staging.social_flood_signals.country``.
_COUNTRY_NAMES: Final[dict[str, re.Pattern[str]]] = {
    canonical: re.compile(pattern, re.IGNORECASE)
    for canonical, pattern in {
        # Anglosphere
        "United States": (
            r"\b(?:United\s+States(?:\s+of\s+America)?|U\.?S\.?A\.?|"
            r"(?:the\s+)?U\.?S\.?)\b"
        ),
        "United Kingdom": (
            r"\b(?:United\s+Kingdom|U\.?K\.?|Britain|Great\s+Britain|England|"
            r"Scotland|Wales|Northern\s+Ireland)\b"
        ),
        "Ireland": r"\bIreland\b",
        "Canada": r"\bCanada\b",
        "Australia": r"\bAustralia\b",
        "New Zealand": r"\bNew\s+Zealand\b",
        # Western Europe
        "France": r"\bFrance\b",
        "Germany": r"\bGermany\b",
        "Spain": r"\bSpain\b",
        "Italy": r"\bItaly\b",
        "Portugal": r"\bPortugal\b",
        "Netherlands": r"\b(?:Netherlands|Holland)\b",
        "Belgium": r"\bBelgium\b",
        "Switzerland": r"\bSwitzerland\b",
        "Austria": r"\bAustria\b",
        "Greece": r"\bGreece\b",
        # Nordics
        "Sweden": r"\bSweden\b",
        "Norway": r"\bNorway\b",
        "Denmark": r"\bDenmark\b",
        "Finland": r"\bFinland\b",
        "Iceland": r"\bIceland\b",
        # Central / Eastern Europe
        "Poland": r"\bPoland\b",
        "Czech Republic": r"\b(?:Czech\s+Republic|Czechia)\b",
        "Slovakia": r"\bSlovakia\b",
        "Hungary": r"\bHungary\b",
        "Romania": r"\bRomania\b",
        "Bulgaria": r"\bBulgaria\b",
        "Croatia": r"\bCroatia\b",
        "Serbia": r"\bSerbia\b",
        "Bosnia and Herzegovina": r"\bBosnia(?:\s+and\s+Herzegovina)?\b",
        "Albania": r"\bAlbania\b",
        "Slovenia": r"\bSlovenia\b",
        "North Macedonia": r"\b(?:North\s+Macedonia|Macedonia)\b",
        "Ukraine": r"\bUkraine\b",
        "Belarus": r"\bBelarus\b",
        "Russia": r"\bRussia\b",
        # Middle East & North Africa
        "Turkey": r"\b(?:Turkey|T\u00fcrkiye|Turkiye)\b",
        "Saudi Arabia": r"\bSaudi\s+Arabia\b",
        "United Arab Emirates": r"\b(?:UAE|United\s+Arab\s+Emirates)\b",
        "Qatar": r"\bQatar\b",
        "Oman": r"\bOman\b",
        "Yemen": r"\bYemen\b",
        "Iran": r"\bIran\b",
        "Iraq": r"\bIraq\b",
        "Syria": r"\bSyria\b",
        "Lebanon": r"\bLebanon\b",
        "Jordan": r"\bJordan\b",
        "Israel": r"\bIsrael\b",
        "Palestine": r"\bPalestine\b",
        "Egypt": r"\bEgypt\b",
        "Libya": r"\bLibya\b",
        "Tunisia": r"\bTunisia\b",
        "Algeria": r"\bAlgeria\b",
        "Morocco": r"\bMorocco\b",
        # Sub-Saharan Africa (flood-prone)
        "Sudan": r"\bSudan\b",
        "South Sudan": r"\bSouth\s+Sudan\b",
        "Ethiopia": r"\bEthiopia\b",
        "Somalia": r"\bSomalia\b",
        "Kenya": r"\bKenya\b",
        "Uganda": r"\bUganda\b",
        "Tanzania": r"\bTanzania\b",
        "Rwanda": r"\bRwanda\b",
        "Burundi": r"\bBurundi\b",
        "Nigeria": r"\bNigeria\b",
        "Ghana": r"\bGhana\b",
        "Senegal": r"\bSenegal\b",
        "Mali": r"\bMali\b",
        "Niger": r"\bNiger\b",
        "Chad": r"\bChad\b",
        "Cameroon": r"\bCameroon\b",
        "Democratic Republic of the Congo": (
            r"\b(?:DRC|Democratic\s+Republic\s+of\s+(?:the\s+)?Congo|DR\s+Congo)\b"
        ),
        "Mozambique": r"\bMozambique\b",
        "Madagascar": r"\bMadagascar\b",
        "Zambia": r"\bZambia\b",
        "Zimbabwe": r"\bZimbabwe\b",
        "Malawi": r"\bMalawi\b",
        "Angola": r"\bAngola\b",
        "South Africa": r"\bSouth\s+Africa\b",
        # Asia (flood-prone)
        "Pakistan": r"\bPakistan\b",
        "Afghanistan": r"\bAfghanistan\b",
        "India": r"\bIndia\b",
        "Nepal": r"\bNepal\b",
        "Bhutan": r"\bBhutan\b",
        "Bangladesh": r"\bBangladesh\b",
        "Sri Lanka": r"\bSri\s+Lanka\b",
        "Maldives": r"\bMaldives\b",
        "China": r"\bChina\b",
        "Japan": r"\bJapan\b",
        "South Korea": r"\bSouth\s+Korea\b",
        "North Korea": r"\bNorth\s+Korea\b",
        "Mongolia": r"\bMongolia\b",
        "Vietnam": r"\b(?:Vietnam|Viet\s+Nam)\b",
        "Cambodia": r"\bCambodia\b",
        "Laos": r"\bLaos\b",
        "Thailand": r"\bThailand\b",
        "Myanmar": r"\b(?:Myanmar|Burma)\b",
        "Malaysia": r"\bMalaysia\b",
        "Singapore": r"\bSingapore\b",
        "Indonesia": r"\bIndonesia\b",
        "Philippines": r"\bPhilippines\b",
        # Americas (other)
        "Mexico": r"\bMexico\b",
        "Guatemala": r"\bGuatemala\b",
        "Honduras": r"\bHonduras\b",
        "Nicaragua": r"\bNicaragua\b",
        "Costa Rica": r"\bCosta\s+Rica\b",
        "Panama": r"\bPanama\b",
        "Cuba": r"\bCuba\b",
        "Haiti": r"\bHaiti\b",
        "Dominican Republic": r"\bDominican\s+Republic\b",
        "Brazil": r"\bBrazil\b",
        "Argentina": r"\bArgentina\b",
        "Chile": r"\bChile\b",
        "Peru": r"\bPeru\b",
        "Colombia": r"\bColombia\b",
        "Venezuela": r"\bVenezuela\b",
        "Ecuador": r"\bEcuador\b",
        "Bolivia": r"\bBolivia\b",
        "Paraguay": r"\bParaguay\b",
        "Uruguay": r"\bUruguay\b",
    }.items()
}


# Adjective form -> canonical country. Adjectives are word-bounded and
# case-insensitive; "French floods" -> France.
_COUNTRY_ADJECTIVES: Final[dict[str, str]] = {
    "American": "United States",
    "British": "United Kingdom",
    "French": "France",
    "German": "Germany",
    "Spanish": "Spain",
    "Italian": "Italy",
    "Portuguese": "Portugal",
    "Dutch": "Netherlands",
    "Belgian": "Belgium",
    "Swiss": "Switzerland",
    "Austrian": "Austria",
    "Greek": "Greece",
    "Polish": "Poland",
    "Czech": "Czech Republic",
    "Hungarian": "Hungary",
    "Romanian": "Romania",
    "Bulgarian": "Bulgaria",
    "Croatian": "Croatia",
    "Serbian": "Serbia",
    "Albanian": "Albania",
    "Ukrainian": "Ukraine",
    "Russian": "Russia",
    "Turkish": "Turkey",
    "Egyptian": "Egypt",
    "Libyan": "Libya",
    "Tunisian": "Tunisia",
    "Algerian": "Algeria",
    "Moroccan": "Morocco",
    "Sudanese": "Sudan",
    "Ethiopian": "Ethiopia",
    "Kenyan": "Kenya",
    "Ugandan": "Uganda",
    "Tanzanian": "Tanzania",
    "Nigerian": "Nigeria",
    "Ghanaian": "Ghana",
    "Senegalese": "Senegal",
    "Cameroonian": "Cameroon",
    "Mozambican": "Mozambique",
    "Malagasy": "Madagascar",
    "Zambian": "Zambia",
    "Zimbabwean": "Zimbabwe",
    "Angolan": "Angola",
    "Pakistani": "Pakistan",
    "Afghan": "Afghanistan",
    "Indian": "India",
    "Nepali": "Nepal",
    "Bangladeshi": "Bangladesh",
    "Sri Lankan": "Sri Lanka",
    "Chinese": "China",
    "Japanese": "Japan",
    "Korean": "South Korea",
    "Mongolian": "Mongolia",
    "Vietnamese": "Vietnam",
    "Cambodian": "Cambodia",
    "Thai": "Thailand",
    "Burmese": "Myanmar",
    "Malaysian": "Malaysia",
    "Singaporean": "Singapore",
    "Indonesian": "Indonesia",
    "Filipino": "Philippines",
    "Mexican": "Mexico",
    "Brazilian": "Brazil",
    "Argentine": "Argentina",
    "Argentinian": "Argentina",
    "Chilean": "Chile",
    "Peruvian": "Peru",
    "Colombian": "Colombia",
    "Venezuelan": "Venezuela",
    "Cuban": "Cuba",
    "Haitian": "Haiti",
    "Canadian": "Canada",
    "Australian": "Australia",
    "Iranian": "Iran",
    "Iraqi": "Iraq",
    "Syrian": "Syria",
    "Lebanese": "Lebanon",
    "Jordanian": "Jordan",
    "Israeli": "Israel",
    "Palestinian": "Palestine",
    "Yemeni": "Yemen",
    "Saudi": "Saudi Arabia",
}


# US state two-letter code -> full state name. Used both for the
# ``"<City>, <ST>"`` pattern and for the full-name fallback.
_US_STATES: Final[dict[str, str]] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}

# US timezone abbreviations -> the region label we surface as place_name.
# Matching is case-sensitive (the codes are always upper-case in practice)
# to avoid false positives on common English words like "est" or "mst".
_US_TIMEZONES: Final[dict[str, str]] = {
    "AKDT": "Alaska",
    "AKST": "Alaska",
    "HST": "Hawaii",
    "HDT": "Hawaii",
    "EDT": "Eastern US",
    "EST": "Eastern US",
    "CDT": "Central US",
    "CST": "Central US",
    "MDT": "Mountain US",
    "MST": "Mountain US",
    "PDT": "Pacific US",
    "PST": "Pacific US",
}

# Pre-compiled patterns built once at import time so the per-row cost stays
# low when transforming thousands of rows in one pass.
_CITY_STATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    # "<City>, <ST>" with a 1- to 4-word title-cased city followed by a
    # comma, optional space, and a 2-letter all-caps state code.
    r"\b([A-Z][a-zA-Z\u00C0-\u017F]+(?:\s+[A-Z][a-zA-Z\u00C0-\u017F]+){0,3}),\s+([A-Z]{2})\b"
)


def _match_country_name(text: str) -> str | None:
    for canonical, pattern in _COUNTRY_NAMES.items():
        if pattern.search(text):
            return canonical
    return None


def _match_country_adjective(text: str) -> str | None:
    # Sort by length descending so multi-word adjectives ("Sri Lankan") win
    # before any shorter substring of the same root.
    for adj in sorted(_COUNTRY_ADJECTIVES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(adj)}\b", text):
            return _COUNTRY_ADJECTIVES[adj]
    return None


def _match_city_state(text: str) -> tuple[str, str] | None:
    """Find a ``<City>, <ST>`` mention with a known state code."""
    for match in _CITY_STATE_PATTERN.finditer(text):
        city = match.group(1).strip()
        state_code = match.group(2)
        if state_code in _US_STATES:
            return city, _US_STATES[state_code]
    return None


def _match_state_name(text: str) -> str | None:
    for code, name in _US_STATES.items():
        # Skip ambiguous bare codes - those are reserved for the
        # ``<City>, <ST>`` pattern. We only match the FULL state name here.
        if re.search(rf"\b{re.escape(name)}\b", text):
            return name
    return None


def _match_us_timezone(text: str) -> str | None:
    # Case-sensitive match because timezone codes are always upper-case in
    # weather alerts ("AKDT", "EDT") whereas the lower-case forms ("est",
    # "cst") are common English words.
    for code, label in _US_TIMEZONES.items():
        if re.search(rf"\b{code}\b", text):
            return label
    return None


def extract_location(text: str | None) -> dict[str, str | None]:
    """Extract ``{country, place_name}`` from free social-post text.

    Strategies are evaluated in precision-first order. The first one that
    fires wins; everything else is skipped. ``{country: None, place_name:
    None}`` is returned when no strategy matches (precision > recall).
    """
    if not text:
        return {"country": None, "place_name": None}

    # 1. Country adjective. Beats explicit names because adjectives carry
    #    stronger sentence-level meaning ("French floods" >> "in France").
    adj_country = _match_country_adjective(text)
    if adj_country is not None:
        return {"country": adj_country, "place_name": None}

    # 2. Explicit country name.
    name_country = _match_country_name(text)
    if name_country is not None:
        return {"country": name_country, "place_name": None}

    # 3. <City>, <ST> pattern - confidently US.
    city_state = _match_city_state(text)
    if city_state is not None:
        city, state_name = city_state
        return {"country": "United States", "place_name": f"{city}, {state_name}"}

    # 4. Full US state name anywhere in the text.
    state_name = _match_state_name(text)
    if state_name is not None:
        return {"country": "United States", "place_name": state_name}

    # 5. US timezone abbreviation (commonly used by NWS weather alerts).
    tz_region = _match_us_timezone(text)
    if tz_region is not None:
        return {"country": "United States", "place_name": tz_region}

    return {"country": None, "place_name": None}

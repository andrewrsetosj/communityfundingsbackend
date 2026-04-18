"""
US state name ↔ abbreviation mapping for location filter expansion.
"""

US_STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

US_STATE_ABBR_TO_NAME = {v: k for k, v in US_STATE_NAME_TO_ABBR.items()}


def expand_state(value: str) -> tuple[str | None, str | None]:
    """
    If `value` matches a US state (full name or 2-letter abbreviation),
    return (full_name_lowercase, abbreviation). Otherwise (None, None).
    """
    norm = value.strip().lower()
    if norm in US_STATE_NAME_TO_ABBR:
        return norm, US_STATE_NAME_TO_ABBR[norm]
    upper = value.strip().upper()
    if upper in US_STATE_ABBR_TO_NAME:
        return US_STATE_ABBR_TO_NAME[upper], upper
    return None, None


USA_ALIASES = {
    "usa", "u.s.a.", "u.s.", "us",
    "united states", "united states of america", "america",
}


def is_usa_filter(value: str) -> bool:
    return value.strip().lower() in USA_ALIASES


def all_state_abbr_regex() -> str:
    """Word-bounded regex alternation matching any US state abbreviation."""
    return r"\y(" + "|".join(US_STATE_NAME_TO_ABBR.values()) + r")\y"


def all_state_ilike_patterns() -> list[str]:
    """ILIKE patterns matching any full US state name, plus common USA aliases."""
    patterns = [f"%{name}%" for name in US_STATE_NAME_TO_ABBR.keys()]
    patterns += ["%usa%", "%united states%", "%america%"]
    return patterns

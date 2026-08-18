"""
Maharashtra / MahaRERA -- the reference implementation.

This module IMPORTS its endpoint tables from config.py rather than copying
them. config.py is currently 100% MahaRERA, and converting it to a
dict-of-dicts would mean editing all six of its existing consumers
(api_client, discover, finalize_report, report, main x2) in the same change
that is supposed to prove "no behaviour change". One definition, zero drift
possible, and test_state_profiles.py asserts the identity so a later
physical move cannot silently diverge.

OUTPUT_ROOT / REQUEST_TIMEOUT / SEARCH_TIMEOUT_MS / PROMOTER_PROJECT_LIMIT
are genuinely state-neutral and stay in config.py permanently.
"""

import config
from .base import (
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_LAND_RECORDS,
    CAP_LOOKUP_BY_REG_NO,
    CAP_ORDERS_SEARCH,
    CAP_PROMOTER_PORTFOLIO,
    CAP_SEPARATE_AUTH,
    StateProfile,
)

# Re-exported, not redefined -- see module docstring.
CATEGORY_ENDPOINTS = config.CATEGORY_ENDPOINTS
CATEGORY_ORDER = config.CATEGORY_ORDER
NO_AUTH_CATEGORIES = config.NO_AUTH_CATEGORIES
BASE_URL = config.BASE_URL
SEARCH_BASE_URL = config.SEARCH_BASE_URL


PROFILE = StateProfile(
    code="MH",
    state_name="Maharashtra",
    rera_acronym="MahaRERA",
    regulator_name="Maharashtra Real Estate Regulatory Authority (MahaRERA)",
    # Maharashtra has many planning authorities (MCGM, MMRDA, PMRDA, CIDCO,
    # NMMC...) and which one applies is a per-project fact the record states,
    # so this stays generic rather than naming one.
    planning_authority_label="the local planning authority",
    # P + 11 digits. NOT unique to Maharashtra -- Telangana uses the same
    # shape (P02400003865). See states/__init__.py::resolve_state for the
    # district-code tiebreak and why it is announced rather than silent.
    reg_no_pattern=r"^P\d{11}$",
    portal_domains=(
        "maharera.maharashtra.gov.in",
        "maharerait.maharashtra.gov.in",
        "mahareat.maharashtra.gov.in",
        "igrmaharashtra.gov.in",
        "bhulekh.mahabhumi.gov.in",
    ),
    # Moved off company_charter._DOMAIN_GENERIC, which had these three rows
    # inline in an otherwise national table.
    domain_labels=(
        ("igrmaharashtra.gov.in", "IGR Maharashtra (Dept. of Registration & Stamps)"),
        ("maharerait.maharashtra.gov.in", "MahaRERA"),
        ("maharera.maharashtra.gov.in", "MahaRERA"),
    ),
    # MahaRERA is the only state offering all seven today. Asserted
    # explicitly in test_state_profiles.py -- mis-declaring one here would
    # silently disable working MahaRERA behaviour.
    capabilities=frozenset({
        CAP_LOOKUP_BY_REG_NO,
        CAP_CATEGORY_API,
        CAP_DOCUMENTS,
        CAP_SEPARATE_AUTH,
        CAP_PROMOTER_PORTFOLIO,
        CAP_ORDERS_SEARCH,
        CAP_LAND_RECORDS,
    }),
)

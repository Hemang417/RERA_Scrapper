"""
Telangana / TG-RERA -- PROFILE ONLY. No adapter yet.

Registered this early for one reason: Telangana is the state whose
registration-number format COLLIDES with Maharashtra's, and
states.resolve_state's tiebreak cannot be tested, or even be meaningful,
against a registry that contains only Maharashtra. Registering the profile
makes the collision real, and the guard in test_reg_no_resolution.py real
with it.

`capabilities` is deliberately EMPTY, and every entry below is a documented
limitation rather than a placeholder:

  * No CAP_LOOKUP_BY_REG_NO. TS-RERA's public record does not display its
    own registration number -- confirmed in our own capture,
    output/CONSTELLA_TS/raw/ts_rera_project.json carries
    official_ts_rera_registration_certificate_number: null plus a gap note
    explaining the number lives behind a separate CAPTCHA-gated "View
    Certificate" link. Search is BY PROJECT NAME.

  * No CAP_SEPARATE_AUTH. TS-RERA CAPTCHA-gates the SEARCH ITSELF, so there
    is no resolve-then-authenticate split to mirror; ts_rera_client
    .search_and_fetch does both at once behind one human solve.

  * No CAP_CATEGORY_API. There is no equivalent of MahaRERA's 9 category
    endpoints -- a project's entire public record is one server-rendered
    "PrintPreview" page.

  * No CAP_DOCUMENTS. The guest view exposes only the promoter's own
    submitted application: no title report, no sanctioned plans, no bank
    NOC, no professional-team certificates.

  * No CAP_ORDERS_SEARCH. Telangana publishes no name-searchable
    complaint/appeal register.

  * No CAP_LAND_RECORDS. Telangana's land system is Dharani, which this
    pipeline does not query.

  * No CAP_PROMOTER_PORTFOLIO. No promoter-name search returning that
    promoter's other projects.

An adapter wrapping ts_rera_client.py is Phase 2 work. Until then, a
Telangana Charter is produced the way CONSTELLA's was: via
run_company_charter(pre_built_facts=...), which needs no adapter at all.
"""

from .base import StateProfile

PROFILE = StateProfile(
    code="TG",
    state_name="Telangana",
    rera_acronym="TG-RERA",
    regulator_name="Telangana Real Estate Regulatory Authority (TG-RERA)",
    # Unlike Maharashtra, the planning authority for the districts we see is
    # consistently HMDA, so naming it is more useful than staying generic.
    planning_authority_label="the Hyderabad Metropolitan Development Authority (HMDA)",
    # SAME SHAPE AS MAHARASHTRA -- P + 11 digits. This is the collision that
    # states.resolve_state exists to handle: MahaRERA P51800077150 vs TG-RERA
    # P02400003865, separable only by the district-code digits.
    reg_no_pattern=r"^P\d{11}$",
    portal_domains=("rerait.telangana.gov.in", "rera.telangana.gov.in"),
    domain_labels=(
        ("rerait.telangana.gov.in", "TG-RERA"),
        ("rera.telangana.gov.in", "TG-RERA"),
    ),
    capabilities=frozenset(),
)

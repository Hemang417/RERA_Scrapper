"""
Tamil Nadu / TNRERA -- profile only (adapter in states/adapter_tamilnadu.py).

THE PATTERN THIS REPO ALREADY CARRIED WAS WRONG IN FOUR WAYS, and its one
recorded example was not a real registration number. states/__init__.py
listed TN as known-but-unsupported with
`^TN/\\d{2}/(?:BUILDING|LAYOUT)/\\d+/\\d{4}$`, evidenced by
"TN/29/Building/0000/2026". A serial of `0000` is a search-box placeholder,
not an issued number -- and every real number checked against that pattern
is a lesson:

  1. THE DISTRICT CODE IS 1 OR 2 DIGITS. Verified live on the authority's
     own 2024 building register: TN/16/Building/0001/2024 sits beside
     TN/01/Building/003/2024. `\\d{2}` misses every single-digit district,
     and 2017's numbers are TN/1/ and TN/2/.
  2. THE SERIAL IS 3 OR 4 DIGITS, INCONSISTENTLY PADDED WITHIN ONE PAGE.
     TN/16/Building/0001/2024 and TN/29/Building/002/2024 are adjacent rows
     on the same register.
  3. THE PREFIX CHANGED. Registrations from 2026 are issued as TNRERA/ with
     abbreviated type tokens: TNRERA/29/BLG/0001/2026, TNRERA/11/LO/0001/2026.
     An adapter handling only the legacy prefix covers about half the corpus.
  4. THE TYPE TOKEN IS NOT JUST Building OR Layout. Also live:
     `Regularisation-Layout` (TN/01/Regularisation-Layout/0028/2022) and
     `Layout/Offline` -- which contains its own slash and so adds a SIXTH
     segment to the number.

AGENTS MUST NOT MATCH. They are TN/Agent/0249/2020: four segments, the
literal word Agent where a project carries its district digits. Requiring
digits in the second slot is what keeps them out.

TWO DATA ERAS, BOTH LIVE, AND THEY ARE SERVED BY DIFFERENT APPLICATIONS.
The legacy register is static per-type-per-year PHP
(`/cms/reg_projects_tamilnadu/Building/<year>.php`, 2017-2025); the current
one is a newer app (`/registered-building/tn`, `/registered-layout/tn`)
whose only control is a year dropdown. Either way a whole year arrives in
one request, with no CAPTCHA and no login -- the same
index-in-one-request shape K-RERA and WBRERA have, sliced by year instead of
by district.

`www.` IS NOT A SYNONYM HERE. `www.rera.tn.gov.in` returns HTTP 403; the
bare host serves. Pin it.

NO PROMOTER SEARCH AND NO PROMOTER ID. The detail routes are keyed by an
opaque per-project UUID obtainable only from a year listing, and the
promoter view lists no portfolio. So a promoter's other Tamil Nadu projects
can only be found by NAME-matching across year tables, which carries exactly
the false-positive exposure guardrails.md records for K-RERA.

WHAT IT PUBLISHES THAT IS GENUINELY USEFUL: a complaint register that NAMES
BOTH PARTIES in their own columns -- verified live on the 2025 judgements
page, 62 rows of Complaint No. / Complainant / Respondent / Project / Date
of Final Order / Order PDF. That makes TNRERA promoter-searchable for
orders, which most authorities in this pipeline are not.

THE PAN IS MASKED (`PAN Card No: XXXXXX465H`) and must not be harvested. One
page was observed leaking a full PAN through a mis-used "Company
Registration No" field, which is a portal bug and not a source: relying on
it would be relying on someone else's mistake, so no PAN capability is
declared.
"""

from .base import (
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_LOOKUP_BY_REG_NO,
    CAP_PROMOTER_PORTFOLIO,
    StateProfile,
)

# The bare host only -- the www. subdomain 403s.
BASE_URL = "https://rera.tn.gov.in"
# Legacy static register, per type per year (2017-2025).
LEGACY_INDEX = BASE_URL + "/cms/reg_projects_tamilnadu/{type}/{year}.php"
LEGACY_TYPES = ("Building", "Normal_Layout", "Regularisation_Layout")
# The current app, one year per request.
ONLINE_BUILDING_INDEX = BASE_URL + "/registered-building/tn"
ONLINE_LAYOUT_INDEX = BASE_URL + "/registered-layout/tn"
# Per-project detail, keyed by an opaque UUID carried in the listing.
PROMOTER_VIEW = BASE_URL + "/public-view1/{kind}/pfirm/{uuid}"
PROJECT_VIEW = BASE_URL + "/public-view2/{kind}/pfirm/{uuid}"
# The order register, and it names complainant AND respondent in their own
# columns -- which is what makes it searchable by promoter.
JUDGEMENTS_INDEX = BASE_URL + "/cms/tnrera_judgements.php"
JUDGEMENTS_YEAR = BASE_URL + "/cms/tnrera_judgements/{year}.php"

PROFILE = StateProfile(
    code="TN",
    state_name="Tamil Nadu",
    rera_acronym="TNRERA",
    regulator_name="Tamil Nadu Real Estate Regulatory Authority (TNRERA)",
    planning_authority_label="the local planning authority",
    # Both eras. Second slot must be DIGITS so TN/Agent/... cannot match;
    # the type token allows the embedded slash of `Layout/Offline`; the
    # serial is \d{3,4} because padding is inconsistent within one page.
    reg_no_pattern=(
        r"^(?:"
        r"TN/\d{1,2}/(?:Building|Layout/Offline|Layout|Regularisation-Layout)/\d{3,4}/\d{4}"
        r"|TNRERA/\d{1,2}/(?:BLG|LO)/\d{3,4}/\d{4}"
        r")$"
    ),
    portal_domains=("rera.tn.gov.in",),
    domain_labels=(("rera.tn.gov.in", "TNRERA"),),
    capabilities=frozenset({
        CAP_LOOKUP_BY_REG_NO,
        CAP_CATEGORY_API,
        CAP_DOCUMENTS,
        # By NAME match across year tables only -- there is no promoter id
        # and no promoter search. The adapter labels every hit a candidate
        # for that reason.
        CAP_PROMOTER_PORTFOLIO,
        # NOT CAP_SEPARATE_AUTH: there is no auth step.
        # NOT CAP_ORDERS_SEARCH: that gates MahaRERA's OWN orders search.
        # TNRERA's judgement register is read directly by the adapter.
        # NOT CAP_LAND_RECORDS: Tamil Nadu's land system is not wired in.
        # The T.S. / survey numbers ON the RERA record are captured, but
        # those are the promoter's declaration.
    }),
)

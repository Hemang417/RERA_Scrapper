"""
Uttar Pradesh / UP-RERA -- profile only (adapter in states/adapter_uttarpradesh.py).

The largest register this pipeline has added since MahaRERA, and the one
that matters most by NCR volume: UP-RERA covers Noida, Greater Noida and
Ghaziabad, which is where a great deal of Delhi-facing development actually
registers (Delhi's own register holds 130 projects -- see states/delhi.py).

THE PATTERN THIS REPO ALREADY CARRIED WAS WRONG, AND WRONG IN THE WAY THAT
MATTERS. states/__init__.py listed UP as a known-but-unsupported authority
with `^UPRERAPRJ\\w+$`. There are TWO live numbering schemes and `\\w` does
not match a slash, so every post-2024 registration failed that pattern,
fell through to the free-text branch and was searched against MahaRERA --
which found nothing, and "not found" reads as "this project does not exist"
rather than "that state was not supported". The exact defect the
unsupported-authority table was built to prevent, hiding inside it.

  * Legacy: UPRERAPRJ14636, UPRERAPRJ11528, UPRERAPRJ2499 -- verified live.
  * Since ~2024: UPRERAPRJ378870/03/2025 -- the trailing pair is the
    registration month and year.

The suffix is DIGITS ONLY and is NOT fixed width: 4 to 6 digits observed.

`PRJ` VS `PRM` IS ONE CHARACTER AND THEY ARE DIFFERENT THINGS. Promoters are
UPRERAPRM31688, UPRERAPRM418917 and so on; a project pattern loosened to
`UPRERAPR\\w+` would swallow them and resolve a promoter id as a project.

WHAT MAKES IT UNUSUALLY CHEAP FOR ITS SIZE: for the legacy scheme the detail
page's `?id=` parameter IS the registration number's numeric suffix.
Verified live -- `Frm_View_Project_Details.aspx?id=14636` serves
UPRERAPRJ14636 -- so resolving a legacy registration number needs NO search
request at all, on a portal whose search is otherwise an ASP.NET WebForms
postback needing __VIEWSTATE round-tripped. Post-2024 numbers do NOT follow
this (`?id=378870` 404s); resolving those is unaudited and the adapter says
so rather than guessing.

WHAT THE DETAIL PAGE PUBLISHES, confirmed live on UPRERAPRJ14636: promoter
name and its UPRERAPRM id, district, status, dates, project cost, KHASRA/PLOT
DETAILS with areas, bank account with IFSC, architect and structural
engineer (often address-only, licence number blank, sometimes literally
"NA"), unit configuration, downloadable documents including the Form REG-3
CA certificate, and quarterly progress certificates.

NO PAN AS READABLE TEXT -- checked across five detail pages. Whether one is
legible inside the annexed REG-3 or affidavit PDFs is unaudited. So UP
cannot widen gst_group's PAN coverage today.

NO PROMOTER-KEYED ORDER REGISTER. The complaint-status page searches by
complaint number only, behind a CAPTCHA, with no promoter or project field;
the "Important Judgement" page rendered empty. So litigation disclosed to
UP-RERA is UNKNOWN for every project here, and that must never read as a
clean litigation record.

TWO OPERATIONAL QUIRKS worth keeping. A second live mirror runs the same
app at uprera.azurewebsites.net, useful when the primary throws its
intermittent HTTP 500. And the id space is SPARSE -- blind enumeration is
mostly 404s, so ids come from the register, never from counting.
"""

from .base import (
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_LOOKUP_BY_REG_NO,
    StateProfile,
)

BASE_URL = "https://www.up-rera.in"
# The same ASP.NET app, useful when the primary host throws its intermittent
# 500. Not a different authority and not a different dataset.
MIRROR_URL = "https://uprera.azurewebsites.net"
# For a LEGACY registration number the id is the number's own numeric
# suffix, so this is reachable without a search request.
PROJECT_DETAIL = "{base}/Frm_View_Project_Details.aspx?id={id}"
PROJECT_LIST = "{base}/View_projects.aspx"
DOCUMENT_URL = "{base}/ViewDocument?Param={name}"

PROFILE = StateProfile(
    code="UP",
    state_name="Uttar Pradesh",
    rera_acronym="UP-RERA",
    regulator_name="Uttar Pradesh Real Estate Regulatory Authority (UP-RERA)",
    # Noida, Greater Noida and Ghaziabad each have their own development
    # authority and which applies is a per-project fact, so this stays
    # generic rather than naming one.
    planning_authority_label="the local development authority",
    # UPRERAPRJ + 3-7 digits, with an optional /MM/YYYY tail used since
    # ~2024. `PRJ` spelled out because promoters are UPRERAPRM and differ by
    # one character.
    reg_no_pattern=r"^UPRERAPRJ\d{3,7}(?:/\d{2}/\d{4})?$",
    portal_domains=("up-rera.in", "www.up-rera.in", "uprera.azurewebsites.net"),
    domain_labels=(
        ("up-rera.in", "UP-RERA"),
        ("www.up-rera.in", "UP-RERA"),
        ("uprera.azurewebsites.net", "UP-RERA"),
    ),
    capabilities=frozenset({
        CAP_LOOKUP_BY_REG_NO,
        CAP_CATEGORY_API,
        CAP_DOCUMENTS,
        # NOT CAP_PROMOTER_PORTFOLIO. The state promoter dropdown enumerates
        # every registered promoter with its UPRERAPRM id, but it is paired
        # with a MANDATORY district filter, so a promoter's projects across
        # 75 districts would need 75 postback requests. Returning a
        # one-district slice as a portfolio is the mistake Gujarat shipped
        # once; the adapter returns None and says why.
        # NOT CAP_SEPARATE_AUTH: the register and detail pages need no auth.
        # NOT CAP_ORDERS_SEARCH: that gates MahaRERA's OWN orders search,
        # and UP publishes no promoter-keyed register at all.
        # NOT CAP_LAND_RECORDS: UP's Bhulekh is not wired in. The khasra
        # numbers ON the RERA record are captured, but those are the
        # promoter's declaration, not an independent land record.
    }),
)

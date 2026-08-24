"""
Delhi / Delhi-RERA -- profile only (adapter in states/adapter_delhi.py).

THE FIRST THING ANY READER OF A DELHI SECTION MUST BE TOLD: THE REGISTER HAS
130 PROJECTS IN IT. Total, for the whole National Capital Territory, across
2018-2026. Confirmed live 2026-08-24 by counting the rows of the authority's
own register (131 rows including the header). MahaRERA carries ~55,000.

That is not a scraping limitation, it is what Delhi is: the city's real
estate is overwhelmingly resale and plotted development that falls outside
the registration thresholds, and the authority has registered 712 AGENTS
against those 130 projects. So a promoter with genuine Delhi activity may
legitimately have no Delhi-RERA record at all, and an absence here is worth
close to nothing as evidence. The adapter says so out loud, for the same
reason states/westbengal.py states its post-2021 start date.

WHERE IT LIVES, WHICH IS NOT WHERE YOU WOULD LOOK. The public search app is
NOT on the gov.in domain -- it runs on the CDAC "eRERA" platform at
erera.co.in. `rera.delhi.gov.in` refused TLS outright when probed; whether
that is geo-fencing, an outage or permanent is unaudited, so it is not used.
The same codebase serves Punjab, so these route names may port.

WHAT MAKES IT CHEAP: ONE GET IS THE ENTIRE REGISTER. No CAPTCHA, no login,
no pagination -- `PublicView/RegisteredProjectDetail` returns all 130 rows
with district, project name, PROMOTER name, registration number, validity
and project type. That is the same whole-state-index-in-one-request shape
K-RERA and WBRERA have, and here it is small enough to be trivial.

WHAT IS NOT AVAILABLE, AND WHY THE PROFILE IS SO THIN. The register's own
"View Details" control is INERT: rendered as
`<a href="javascript:void(0);" id="modalOpenerButton">View</a>` with no
href, no data- attribute, and -- verified by searching the served page --
no ajax call, no url: literal and no detail route referenced anywhere in
it. Six plausible detail routes were probed and every one returned 404. So
land details, escrow accounts, professionals of record, documents and
quarterly progress are not merely unfetched, they are not reachable through
this interface at all. Declaring any of them would be the mistake Gujarat
made once: a capability the adapter cannot deliver.

Registration format, every one of the 130 uniform (confirmed live):

    DLRERA2026P0012   DLRERA2025P0013   DLRERA2018P0001

i.e. DLRERA + 4-digit year + P + 4-digit serial. Years 2018-2026; nothing
earlier. AGENTS use the same shape with `A`, e.g. DLRERA2026A0072, so the
literal `P` is what keeps a project pattern from capturing agents. There
are gaps in the issued numbers, so a miss is not proof of non-existence.

A NOTE ON THE ACRONYM. This authority calls itself Delhi-RERA or RERA
Delhi, never "DRERA", and "DRERA" is used loosely in the wild for other
bodies -- Uttarakhand's authority (UK-RERA, seated at Dehradun) among them.
The ambiguous acronym is deliberately kept out of the resolver; the state
code is DL.
"""

from .base import (
    CAP_LOOKUP_BY_REG_NO,
    CAP_PROMOTER_PORTFOLIO,
    StateProfile,
)

BASE_URL = "https://erera.co.in/reradelhiindex"
# The whole register, one GET, no CAPTCHA. 130 projects.
STATE_INDEX = BASE_URL + "/PublicView/RegisteredProjectDetail"
EXTENSIONS_INDEX = BASE_URL + "/PublicView/RegisteredProjectExtensionsDetail"
PENDING_INDEX = BASE_URL + "/PublicView/ProjectPendingApplicationsDetail"
AGENTS_INDEX = BASE_URL + "/PublicView/RegisteredAgentsDetail"
# The complaint register, and it NAMES BOTH PARTIES in their own columns --
# Complaint Number / Complainant Name / Respondent Name / Date of Decision.
# The misspelling `Compalaint` is in the real URL; it is not a typo here.
ORDER_REGISTER = BASE_URL + "/CourtView/OrderJudgementsAuthorityInfo?type=GC_Compalaint_M"

PROFILE = StateProfile(
    code="DL",
    state_name="Delhi",
    rera_acronym="Delhi-RERA",
    regulator_name="Real Estate Regulatory Authority for NCT of Delhi (Delhi-RERA)",
    planning_authority_label="the Delhi Development Authority (DDA)",
    # DLRERA<year>P<serial>. The literal P is load-bearing: agents are
    # DLRERA2026A0072 and must not match a project pattern.
    reg_no_pattern=r"^DLRERA20\d{2}P\d{4}$",
    portal_domains=("erera.co.in", "rera.delhi.gov.in"),
    domain_labels=(
        ("erera.co.in", "Delhi-RERA"),
        ("rera.delhi.gov.in", "Delhi-RERA"),
    ),
    capabilities=frozenset({
        CAP_LOOKUP_BY_REG_NO,
        # The register names the promoter against every project, so a
        # promoter's other Delhi projects are a local join over 130 rows --
        # strictly better than a promoter-search endpoint at this size.
        CAP_PROMOTER_PORTFOLIO,
        # NOT CAP_CATEGORY_API and NOT CAP_DOCUMENTS: there is no reachable
        # per-project record at all. See the module docstring -- the
        # register's own View control is inert and every detail route
        # probed returned 404.
        # NOT CAP_SEPARATE_AUTH: there is no auth step.
        # NOT CAP_ORDERS_SEARCH: that capability gates MahaRERA's OWN orders
        # search. Delhi's complaint register does name both parties and the
        # adapter reads it directly.
        # NOT CAP_LAND_RECORDS: Delhi's land records are not wired in.
    }),
)

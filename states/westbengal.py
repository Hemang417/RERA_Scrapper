"""
West Bengal / WBRERA -- profile only (adapter in states/adapter_westbengal.py).

Built alongside Jharkhand for the same subject-first reason: the promoter
that drove this pan-India work has twelve West Bengal-registered entities in
its corporate graph, more than any state except Maharashtra itself.

A NOTE ON WHY THIS REGISTER IS YOUNG. West Bengal ran its own statute, the
Housing Industry Regulation Act (HIRA), instead of adopting RERA. The
Supreme Court struck HIRA down in May 2021 as repugnant to the central Act,
and WBRERA was constituted afterwards. So the register begins in earnest
around 2023, and a West Bengal project completed before then may have no
WBRERA record at all while being perfectly genuine. That is a property of
the regulator's history, not a gap in the promoter's disclosure, and any
absence found here has to be read in that light.

A PHP application. No CAPTCHA, no token, no login. Two quirks:

  * LEGACY TLS RENEGOTIATION IS REQUIRED, exactly as for GujRERA -- plain
    `requests` fails the handshake outright. See the adapter, which uses
    urllib3 directly for the same reason states/adapter_gujarat.py does.
  * THERE IS NO SEARCH BOX. The register is browsed by district. But
    `district_project.php?dcode=0` returns EVERY district at once, so the
    whole state index -- 4,721 projects, confirmed live -- arrives in a
    single request, the same shape K-RERA's client-side index has. Resolving
    a registration number is therefore an index lookup, not a search.

The state index carries project id, name, completion date, registration
number and date, but NOT the promoter, so a promoter's other projects can
only be found by opening detail pages. That is 4,721 requests for a full
sweep, which this adapter deliberately does not do; see its
_promoter_portfolio for what it does instead and what it says about it.

Registration format:

    WBRERA/P/NOR/2024/002162
    WBRERA / P / <district code> / <year> / <serial>

with an internal project id of the form WBRERA/NPR-003009. Neither can
collide with another registered state's format.
"""

from .base import (
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_LOOKUP_BY_REG_NO,
    StateProfile,
)

BASE_URL = "https://rera.wb.gov.in"
STATE_INDEX = BASE_URL + "/district_project.php?dcode=0"
PROJECT_DETAIL = BASE_URL + "/project_details.php?procode={}"
AGENT_LIST = BASE_URL + "/agent_list.php?dcode=0"
# WBRERA's ORDER REGISTER: 4,881 orders in one request, but keyed ONLY by
# complaint number -- no party is named in any column. The promoter is
# inside the order PDF, and at ~900 KB each those are not sweepable.
ORDER_REGISTER = BASE_URL + "/authority_order.php"
AUTHORITY_DECISIONS = BASE_URL + "/authority_decisions.php"
# The CAUSE LISTS are what make the register joinable: each PDF tabulates
# Complaint No. / Complainant / Respondent, so they map a complaint number
# to the promoter it was filed against.
CAUSE_LIST = BASE_URL + "/cause_list.php"
# 17 rejected/defaulting applications, keyed by NAME. Cheap and directly
# useful, unlike the orders.
DEFAULTERS = BASE_URL + "/defaulter.php"

PROFILE = StateProfile(
    code="WB",
    state_name="West Bengal",
    rera_acronym="WBRERA",
    regulator_name="West Bengal Real Estate Regulatory Authority (WBRERA)",
    planning_authority_label="the local planning authority",
    # WBRERA/P/<district>/<year>/<serial>. The district segment is letters
    # of varying length (NOR, DAR, KOL...), the serial is zero-padded.
    reg_no_pattern=r"^WBRERA/[A-Z]/[A-Z]{2,4}/\d{4}/\d{4,7}$",
    portal_domains=("rera.wb.gov.in",),
    domain_labels=(("rera.wb.gov.in", "WBRERA"),),
    capabilities=frozenset({
        CAP_LOOKUP_BY_REG_NO,
        CAP_CATEGORY_API,
        CAP_DOCUMENTS,
        # NOT CAP_PROMOTER_PORTFOLIO. The state index does not carry the
        # promoter, and there is no promoter search, so a promoter's other
        # projects are not derivable without opening all 4,721 detail
        # pages. Declaring this and returning something partial is the
        # mistake Gujarat made once; the adapter returns None and says why.
        # NOT CAP_SEPARATE_AUTH: there is no auth step.
        # NOT CAP_ORDERS_SEARCH: that gates MahaRERA's own orders search.
        # NOT CAP_LAND_RECORDS: West Bengal's system is Banglarbhumi, not
        # wired in.
    }),
)

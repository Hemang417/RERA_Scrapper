"""
Karnataka / K-RERA -- profile only (adapter in states/adapter_karnataka.py).

A server-rendered Java/JSP application, nothing like Gujarat's Angular SPA
or MahaRERA's JSON microservices -- which is exactly why the StateAdapter
seam is a Protocol rather than a shared base class. No CAPTCHA, no token,
plain HTTPS with no TLS quirks.

Two things it does BETTER than MahaRERA, both confirmed live:

  * The project-search page embeds the ENTIRE state index client-side --
    8,887 approved projects as four parallel JavaScript arrays giving
    acknowledgement number, registration number, project name and promoter
    name. That is a complete project-to-promoter map for the whole state in
    ONE request, so a promoter's other projects are directly derivable.
    Gujarat, by contrast, publishes no promoter-to-projects link at all.

  * The project detail page carries land-owner shares and survey numbers,
    cost incurred vs estimated, delay reasons, and NOC expiry/renewal
    tracking -- none of which MahaRERA publishes.

Registration format (distinctive, cannot collide with another state):

    PRM/KA/RERA/1251/446/PR/040826/008858
    PRM / KA / RERA / tier / ward / PR / ddmmyy / serial

Acknowledgement numbers share the shape but start ACK/, and are a separate
identifier -- the portal search accepts either.
"""

from .base import (
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_LOOKUP_BY_REG_NO,
    CAP_PROMOTER_PORTFOLIO,
    CAP_SEPARATE_AUTH,
    StateProfile,
)

BASE_URL = "https://rera.karnataka.gov.in"
SEARCH_PAGE = BASE_URL + "/viewAllProjects"
SEARCH_POST = BASE_URL + "/projectViewDetails"
DETAIL_POST = BASE_URL + "/projectDetails"
COMPLAINT_POST = BASE_URL + "/projectComplaintDetails"
# The STATE-WIDE complaint register -- one request, every project that has
# a complaint, with its count. This is the authoritative source; see the
# adapter's _complaint_count for why the per-project page is not.
COMPLAINT_REPORT = BASE_URL + "/projectComplaintReport"
# The STATE-WIDE ORDER-SEARCH index, in one request, keyed by promoter --
# 11,732 entries across 1,821 promoters when first read on 2026-08-21. The
# page's own POST (/viewJudgementDetails) does NOT filter server-side: a
# real firm name and a nonsense one return byte-identical pages apart from
# the visitor counter, because the whole register ships to the browser and
# is filtered there. Wiring that POST up as a search would have returned
# "no orders" for every promoter ever queried.
ORDERS_PAGE = BASE_URL + "/viewAllJudgements"
# The rest of K-RERA's order and complaint registers, each a whole-state
# table in one request. Read together they are the closest thing any
# authority in this pipeline publishes to a promoter's regulatory history.
# The penalty table inside PROJECT_ORDERS_PAGE is the single most useful:
# it names the violation, the section, and the amount.
INTERIM_ORDERS_PAGE = BASE_URL + "/viewAllInterimOrders"
PROJECT_ORDERS_PAGE = BASE_URL + "/viewAllProjectOrders"
AO_ORDERS_PAGE = BASE_URL + "/viewAllAOorders"
COMPLAINT_DETAILS_PAGE = BASE_URL + "/viewAllComplaintDetails"
CERTIFICATE_URL = BASE_URL + "/certificate?CER_NO={}"
DOWNLOAD_URL = BASE_URL + "/download_jc?DOC_ID={}"

PROFILE = StateProfile(
    code="KA",
    state_name="Karnataka",
    rera_acronym="K-RERA",
    regulator_name="Karnataka Real Estate Regulatory Authority (K-RERA)",
    planning_authority_label="the local planning authority",
    # PRM/KA/RERA/<tier>/<ward>/PR/<ddmmyy>/<serial>, or the ACK/ variant.
    reg_no_pattern=r"^(?:PRM|ACK)/KA/RERA/[\w\-]+/[\w\-]+/[A-Z]{2}/\d{6}/\d+$",
    portal_domains=("rera.karnataka.gov.in",),
    domain_labels=(("rera.karnataka.gov.in", "K-RERA"),),
    capabilities=frozenset({
        CAP_LOOKUP_BY_REG_NO,
        # "Category API" in the sense the pipeline means it: distinct,
        # separately-addressable record sets per project. They arrive as
        # HTML tables rather than JSON, which is the adapter's problem, not
        # a difference the rest of the pipeline can see.
        CAP_CATEGORY_API,
        CAP_DOCUMENTS,
        CAP_SEPARATE_AUTH,
        # Derivable from the client-side state index -- see module docstring.
        CAP_PROMOTER_PORTFOLIO,
        # NOT CAP_ORDERS_SEARCH. K-RERA does publish judgements at
        # /viewAllJudgements, but this pipeline has no scraper for them --
        # and that capability gates MahaRERA's OWN orders search, so
        # declaring it true made a Karnataka project fire a MahaRERA search
        # against a portal that could never match it. Complaint COUNTS come
        # from the state-wide register instead; see the adapter.
        # No CAP_LAND_RECORDS: Karnataka's land system is Bhoomi, not wired
        # in. The RERA record's own survey numbers ARE captured, but that is
        # the promoter's declaration, not an independent land record.
    }),
)

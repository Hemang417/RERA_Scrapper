"""
Gujarat / GujRERA -- profile only (the adapter is states/adapter_gujarat.py).

The most automatable state portal found so far, and in some respects richer
than MahaRERA:

  * Public search needs NO login and NO CAPTCHA. The portal says so itself
    on the landing page: "Public Search (No Login): Browse registered
    Projects, Agents, and Professionals without signing in."
  * A project CAN be found by its registration number -- an exact-string
    search returns exactly that project -- so CAP_LOOKUP_BY_REG_NO holds.
  * It publishes AUDITED BALANCE SHEETS, profit-and-loss statements and
    income-tax returns per project (getproject-doc's `findoc` block).
    MahaRERA exposes none of that.

The registration format is distinctive and cannot collide with any other
registered state:

    PR/GJ/SURAT/SURAT CITY/SUDA/PAA12907/120224/311228
    PR/GJ/AHMEDABAD/AHMEDABAD CITY/AUDA/RAA00648/091117

i.e. PR / GJ / district / sub-district / planning authority / code / dates.
Note the embedded spaces -- the pattern must tolerate them, and it is
matched case-insensitively.
"""

from .base import (
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_LOOKUP_BY_REG_NO,
    CAP_SEPARATE_AUTH,
    StateProfile,
)

BASE_URL = "https://gujrera.gujarat.gov.in"
PROJECT_REG_API = BASE_URL + "/project_reg/"
DMS_METADATA_URL = BASE_URL + "/vdms/getDocMetadata/{}"
# Recovered from the app bundle's fileDownloadAPI: /vdms/getDoc/ returns
# 403, /vdms/download/ serves the real PDF bytes.
DMS_DOWNLOAD_URL = BASE_URL + "/vdms/download/{}"

PROFILE = StateProfile(
    code="GJ",
    state_name="Gujarat",
    rera_acronym="GujRERA",
    regulator_name="Gujarat Real Estate Regulatory Authority (GujRERA)",
    # Gujarat's approving authorities are per-city development authorities
    # (AUDA, SUDA, VUDA) or the municipal corporation, and which one applies
    # is embedded in the registration number itself, so this stays generic.
    planning_authority_label="the local development authority",
    # PR/GJ/<district>/<sub-district>/<authority>/<code>/<dates>. Spaces are
    # real (e.g. "SURAT CITY"), hence [^/]+ rather than \\w+.
    reg_no_pattern=r"^PR/GJ/[^/]+/[^/]+/[^/]+/[^/]+/[\d/]+$",
    portal_domains=("gujrera.gujarat.gov.in", "gujrerar1.gujarat.gov.in"),
    domain_labels=(
        ("gujrera.gujarat.gov.in", "GujRERA"),
        ("gujrerar1.gujarat.gov.in", "GujRERA"),
    ),
    capabilities=frozenset({
        CAP_LOOKUP_BY_REG_NO,
        CAP_CATEGORY_API,
        CAP_DOCUMENTS,
        # No CAPTCHA at all, so resolve and auth are trivially separable --
        # there is simply no auth step.
        CAP_SEPARATE_AUTH,
        # NO CAP_PROMOTER_PORTFOLIO -- confirmed live, and worth recording
        # because the opposite looks true at first glance. The global search
        # does return PROMOTER entities, but a promoter's own projects are
        # NOT reachable from one: searching the promoter name of a known
        # project ("AALEKH ENTERPRISE", promoter of project 17020) returns
        # the promoter row and ZERO projects, and the dedicated
        # public/projectAllApplications/<promoterId> endpoint returns an
        # empty list. Matching projects to a promoter by name text would
        # INVENT links rather than find them -- the same reasoning
        # company_charter.find_group_companies_by_cin uses to refuse a
        # directory listing as a related-party set.
        #
        # No CAP_ORDERS_SEARCH: judgements exist on the portal but behind a
        # separate flow this adapter does not yet query.
        # No CAP_LAND_RECORDS: Gujarat's land system is AnyROR, not wired in.
    }),
)

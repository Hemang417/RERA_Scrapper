"""
Jharkhand / JHARERA -- profile only (adapter in states/adapter_jharkhand.py).

Built subject-first rather than size-first. JHARERA is a small register
(~1,200 projects against MahaRERA's ~55,000), and on project count alone it
would rank near the bottom of the thirty. It was built third-from-last to
last for exactly the reason project count is the wrong metric for group
diligence: the promoter whose Mumbai project drove this whole pan-India
effort is a RANCHI group, and its real track record is here. A
Maharashtra-only read of that promoter shows one project and almost no
history. JHARERA holds the rest.

A server-rendered ASP.NET MVC application. No CAPTCHA, no token, no TLS
quirk, and a plain GET search -- the least hostile portal audited so far.

What it publishes that MahaRERA does NOT, all confirmed live:

  * PAN NUMBERS AS TEXT FIELDS, for the promoter's contractor, architect
    and structural engineer. Maharashtra and Gujarat only ever file the PAN
    CARD as a scanned document (see promoter_identity), so this is the first
    built state where a joinable national identifier is simply readable.
  * A DECLARED PAST-PROJECTS table on the project page, giving the
    promoter's earlier registrations with their own numbers.
  * A LITIGATION table, per project, naming petitioner, respondent, the
    authority and the facts of the case.
  * Audited balance sheets and three years of income-tax returns, as
    downloadable documents.
  * Separate state-wide REJECTED, SURRENDERED and EXTENSION registers, and
    a disposed-complaints register -- none of which MahaRERA publishes. A
    rejected or surrendered registration is diligence material of a kind
    the Maharashtra pipeline has never had access to.

Registration formats -- BOTH are live and they are not the same string:

    JHARERA/PROJECT/35/2023     (current)
    JRERA/PROJECT/08/2018       (older registrations, note the missing HA)

Confirmed live on real records: PRANAMI CREST is JHARERA/PROJECT/35/2023,
and its own declared past project PRANAMI BLUE SAPPHIRE is
JRERA/PROJECT/08/2018. A pattern matching only the longer spelling would
silently fail to resolve every pre-2019 project, so the profile accepts
both. Neither can collide with another registered state's format.
"""

from .base import (
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_LOOKUP_BY_REG_NO,
    CAP_PROMOTER_PORTFOLIO,
    StateProfile,
)

BASE_URL = "https://jharera.jharkhand.gov.in"
PROJECT_LIST = BASE_URL + "/Home/OnlineRegisteredProjectsList"
PROJECT_DETAIL = BASE_URL + "/Home/ViewProjectProfile/{}"
REJECTED_LIST = BASE_URL + "/Home/RejectedList"
SURRENDERED_LIST = BASE_URL + "/Home/SurrenderedList"
DISPOSED_COMPLAINTS = BASE_URL + "/Home/DisposedComplaintList"
EXTENSION_LIST = BASE_URL + "/Home/PrjtExtensionlist"

PROFILE = StateProfile(
    code="JH",
    state_name="Jharkhand",
    rera_acronym="JHARERA",
    regulator_name="Jharkhand Real Estate Regulatory Authority (JHARERA)",
    planning_authority_label="the local planning authority",
    # Both live spellings. \d{1,4} on the serial because it is not
    # zero-padded consistently ("08" and "144" both occur).
    reg_no_pattern=r"^(?:JHARERA|JRERA)/PROJECT/\d{1,4}/\d{4}$",
    portal_domains=("jharera.jharkhand.gov.in",),
    domain_labels=(("jharera.jharkhand.gov.in", "JHARERA"),),
    capabilities=frozenset({
        CAP_LOOKUP_BY_REG_NO,
        # Distinct, separately-addressable record sets per project, arriving
        # as HTML tables rather than JSON -- the adapter's problem, not a
        # difference anything downstream can see.
        CAP_CATEGORY_API,
        CAP_DOCUMENTS,
        # The search matches promoter as well as project text, so a
        # promoter's other registrations are directly derivable.
        CAP_PROMOTER_PORTFOLIO,
        # NOT CAP_ORDERS_SEARCH: that capability gates MahaRERA's OWN orders
        # search (the mistake Karnataka made once, which sent a Karnataka
        # run at maharera.maharashtra.gov.in). JHARERA's disposed-complaint
        # register is read directly by the adapter instead.
        # NOT CAP_SEPARATE_AUTH: there is no auth step at all.
        # NOT CAP_LAND_RECORDS: Jharkhand's land system is JharBhoomi, not
        # wired in. The plot/khata/thana numbers on the RERA record ARE
        # captured, but those are the promoter's declaration, not an
        # independent land record.
    }),
)

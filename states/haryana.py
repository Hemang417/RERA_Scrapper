"""
Haryana / HARERA -- profile only (adapter in states/adapter_haryana.py).

THIS STATE WAS DELIBERATELY REFUSED ONCE, AND THAT REFUSAL WAS WRONG.
states/__init__.py carried a note that Haryana's portal "published no
registration number this could be derived from on 2026-08-21, and a guessed
pattern would either miss real numbers or capture someone else's". The
caution was right; the conclusion was not. It was drawn from
hareraggm.gov.in -- Gurugram's own separate app, whose project list renders
headers and no rows without JavaScript. The SHARED portal at
haryanarera.gov.in serves the whole register as plain server-rendered HTML,
registration numbers included. Verified live 2026-08-24: 1,087 Panchkula
rows and 1,074 Gurugram rows, in one GET each.

ONE PORTAL, TWO BENCHES, THREE NUMBER FORMATS. The bench is a path suffix,
not a separate host: `/admincontrol/registered_projects/1` is Panchkula and
`/2` is Gurugram, off one record store. But the benches number their
certificates differently, and there is a third identifier that is the only
sane thing to resolve on.

  * `Project ID` -- RERA-PKL-1825-2025, RERA-GRG-741-2020. Uniform across
    both benches and every era, and it is what the portal's own search
    form accepts. THIS IS THE RESOLVER KEY.
  * `Registration Certificate Number` -- the messy one, and the reason the
    first audit gave up. Panchkula prints HRERA-PKL-AMB-812-2025 (with a
    district code); Gurugram prints GGM/415/147/2020/31 DATED 09.10.2020;
    the certificate PDF prints the same Gurugram number fully qualified as
    RC/REP/HARERA/GGM/860/592/2024/87. All three are the same identifier at
    different truncations.
  * PRE-2018 LEGACY, and it is NOT matchable: `211 OF 2017 DATED
    18.09.2017`. A pattern for that shape would capture almost any
    numbered document, so those registrations resolve by Project ID only.
    One row even carries a trailing stray character
    ("GGM/486/218/2021/54 DATED 21.09.2021 L"), which is why the
    certificate number is a display field here and never a key.

WHAT IT PUBLISHES THAT NOTHING ELSE IN THIS PIPELINE DOES: **THE CIN**.
Confirmed live on RERA-GRG-741-2020, whose record states
`CIN No. ... U70101DL1996PTC075865`. Every other authority audited publishes
neither CIN nor DIN -- see docs/PAN_INDIA_PROGRESS.md's join-key table --
which forces this pipeline to link RERA records to MCA records by NAME and
carry the false-positive risk that implies. Haryana states the join key
outright, so a Haryana project is the one case where promoter-to-corporate
identity is a hard link rather than a name match.

Also on the detail record, all confirmed live: registered address, MD/CEO
and each director by name, bank account number with IFSC and MICR, project
cost against amount received from allottees, licence numbers, an explicit
"whether any litigation is pending against the Project" field, three years
of annexed balance sheets, and units total/booked/unsold.

THE PAN IS MASKED AND MUST NOT BE HARVESTED. It renders as `XXXX280H` --
last four characters only, for the company and for every director.
Certificate PDFs are scans, so there is no text PAN there either. Haryana
therefore cannot widen gst_group's PAN coverage, and nothing here should
try: a four-character tail is not a PAN.
"""

from .base import (
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_LOOKUP_BY_REG_NO,
    CAP_PROMOTER_PORTFOLIO,
    StateProfile,
)

BASE_URL = "https://haryanarera.gov.in"
# Bench is a path suffix on one shared register: 1 = Panchkula, 2 = Gurugram.
BENCHES = {"1": "Panchkula", "2": "Gurugram"}
REGISTERED_PROJECTS = BASE_URL + "/admincontrol/registered_projects/{}"
LAPSED_PROJECTS = BASE_URL + "/admincontrol/lapsed_projects/{}"
# NOT the same list as LAPSED_PROJECTS -- verified live 2026-08-24 audit.
# Lapsed = the registration's own Approval-To validity date has passed.
# Cancelled = HARERA's menu labels this "Defaulter/ Cancelled/ Suspended/
# Abeyance Projects", an authority action against the promoter. 320+235
# rows lapsed across both benches vs 23+5 cancelled/defaulter.
CANCELLED_PROJECTS = BASE_URL + "/admincontrol/cancelled_projects/{}"
# The detail view takes a plain integer, and that integer is carried in the
# register row itself -- so opening a project needs no search step.
PROJECT_DETAIL = BASE_URL + "/view_project/project_preview_open/{}"
PROJECT_SEARCH_DETAIL = BASE_URL + "/view_project/searchprojectDetail/{}"
# Certificate ids are base64 of the integer, e.g. ODQ0 == "844".
CERTIFICATE = BASE_URL + "/view_project/view_certificate/{}"
# Promoter search that needs NO CAPTCHA (unlike /view_project/search_project,
# which requires one): Builder Name + Builder District.
PROMOTER_SEARCH = BASE_URL + "/assistancecontrol/project_search_public/{}"
# Complaint records name BOTH parties -- complainant and respondent -- but
# the search front door is number+year+CAPTCHA, so there is no browsable
# name-bearing index. The per-case path takes a sequential integer.
CASE_DETAIL = BASE_URL + "/assistancecontrol/searchcasedetailopen/{}"

PROFILE = StateProfile(
    code="HR",
    state_name="Haryana",
    rera_acronym="HARERA",
    regulator_name="Haryana Real Estate Regulatory Authority (HARERA)",
    # Gurugram and Panchkula are the two benches; the licensing authority
    # for most of the register is the Department of Town and Country
    # Planning, which is per-project, so this stays generic.
    planning_authority_label="the Department of Town and Country Planning (DTCP)",
    # The Project ID is the resolver key -- see the module docstring for why
    # the certificate number is not. The HRERA-PKL and GGM certificate forms
    # are accepted too, since a reader is as likely to hold one of those.
    # The pre-2018 "211 OF 2017" form is deliberately UNMATCHED.
    reg_no_pattern=(
        r"^(?:"
        r"RERA-(?:PKL|GRG)-\d+-\d{4}"                     # Project ID, both benches
        r"|HRERA-PKL-[A-Z]{2,4}-\d+-\d{4}"                # Panchkula certificate
        r"|(?:RC/REP/HARERA/)?GGM/\d+/\d+/\d{4}(?:/\d+)?"  # Gurugram certificate
        r")$"
    ),
    portal_domains=("haryanarera.gov.in", "hareraggm.gov.in"),
    domain_labels=(
        ("haryanarera.gov.in", "HARERA"),
        ("hareraggm.gov.in", "HARERA Gurugram"),
    ),
    capabilities=frozenset({
        CAP_LOOKUP_BY_REG_NO,
        # Distinct, separately-addressable record sets per project, arriving
        # as HTML tables rather than JSON -- the adapter's problem, not a
        # difference anything downstream can see.
        CAP_CATEGORY_API,
        CAP_DOCUMENTS,
        # The register names the Builder against every project, so a
        # promoter's other Haryana projects are a local join over the two
        # bench indexes.
        CAP_PROMOTER_PORTFOLIO,
        # NOT CAP_SEPARATE_AUTH: there is no auth step on the register.
        # NOT CAP_ORDERS_SEARCH: that gates MahaRERA's OWN orders search.
        # Haryana's complaint records do name both parties, but there is no
        # browsable name-keyed index to search -- only per-case integers.
        # NOT CAP_LAND_RECORDS: Haryana's land system (Jamabandi) is not
        # wired in, and this record carries licence numbers rather than
        # khasra numbers anyway.
    }),
)

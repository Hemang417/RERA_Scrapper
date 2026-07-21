"""
Central configuration for the MahaRERA scraper.

No login/account/CAPTCHA is required. Two public (unauthenticated) surfaces
are used:

1. https://maharera.maharashtra.gov.in -- the public Drupal site with a
   no-login "Search Project" box (Project Name / MahaRERA Registration
   Number). Used only to resolve a registration number to the portal's
   internal numeric project id, by reading the "View Details" link's href
   from the results page (this href already contains the full public detail
   URL, e.g. https://maharerait.maharashtra.gov.in/public/project/view/23600).

2. https://maharerait.maharashtra.gov.in/api/... -- the actual data API.
   The specific endpoint that follows is individually confirmed to require
   NO Authorization header at all:
       POST /api/maha-rera-public-view-project-registration-service/public/
            projectregistartion/getProjectGeneralDetailsByProjectId
       body: {"projectId": "<id>"}  ->  200 OK with full project JSON.
   The remaining category endpoints below were observed as real, successful
   (200 OK) calls made by the site's own public project-view page while
   loading normally (i.e. they are real endpoint names taken from live
   traffic, not guesses) but weren't each individually re-verified for their
   exact payload/response shape -- hence `status: "observed"` vs
   `status: "confirmed"`. Treat "observed" data with a little more caution
   and sanity-check the raw JSON dumped to output/<reg_no>/raw/ on first use.
"""

SEARCH_BASE_URL = "https://maharera.maharashtra.gov.in"
SEARCH_URL = f"{SEARCH_BASE_URL}/"
SEARCH_INPUT_SELECTOR = "#edit-project-name"
# Scoped to the "Projects" tab's own form -- the site reuses id="edit-submit"
# across multiple hidden per-tab forms (Projects/Promoters/Agent/Complaints),
# so an unscoped #edit-submit can resolve to the wrong (hidden) tab's button.
SEARCH_SUBMIT_SELECTOR = "#projects-form input[value='Search']"
VIEW_DETAILS_LINK_SELECTOR = "a:has-text('View Details')"

# The "Promoters" search tab (same page, different Bootstrap tab panel) --
# confirmed live: searching a promoter name here returns every project
# registered under that promoter, in the exact same result-card format as
# the Projects tab's name search. No Registered/Revoked toggle exists on
# this tab (also confirmed live), unlike the Projects tab.
PROMOTERS_TAB_SELECTOR = "#promoters-tab"
PROMOTER_NAME_INPUT_SELECTOR = "#edit-promoters-name"
PROMOTER_SEARCH_SUBMIT_SELECTOR = "#edit-submit--3"

BASE_URL = "https://maharerait.maharashtra.gov.in"

# The project detail page (not the free JSON API) gates on a homegrown text
# CAPTCHA -- confirmed live: loading this URL directly shows an "Enter the
# CAPTCHA" input + distorted-text image + Submit button before the SPA loads.
# Solving it is what causes the site to mint the guest accessToken into
# sessionStorage. See session_auth.py.
DETAIL_VIEW_URL_TEMPLATE = BASE_URL + "/public/project/view/{}"
CAPTCHA_INPUT_SELECTORS = [
    "input[placeholder*='CAPTCHA' i]",
    "input[aria-label*='CAPTCHA' i]",
]
CAPTCHA_TIMEOUT_SECONDS = 300
CAPTCHA_POLL_INTERVAL_SECONDS = 2.0

# Categories that MahaRERA's API serves with no Authorization header at all.
# Everything else in CATEGORY_ENDPOINTS needs a guest accessToken.
NO_AUTH_CATEGORIES = ["projects", "complaints"]

_PUBLIC_VIEW_PREFIX = "/api/maha-rera-public-view-project-registration-service/public/projectregistartion"

# Real document-download mechanism (recovered from observed working traffic):
# POST {"fileName": ..., "documentId": ...} with the bearer token, NOT a plain
# GET on a static URL. api_client.download_documents() tries this first and
# falls back to scanning for direct URLs for anything that doesn't fit.
DMS_DOWNLOAD_PATH = "/api/maha-rera-dms-service/batch-job/downloadDocumentForPublicView"

# One entry per required category. `path` is the full API path (varies by
# microservice). All are called as POST {"projectId": "<id>"}.
CATEGORY_ENDPOINTS = {
    "projects": {
        "path": f"{_PUBLIC_VIEW_PREFIX}/getProjectGeneralDetailsByProjectId",
        "status": "confirmed",
    },
    "professionals": {
        "path": f"{_PUBLIC_VIEW_PREFIX}/getProjectProfessionalByType",
        "status": "observed",
    },
    "partners": {
        "path": f"{_PUBLIC_VIEW_PREFIX}/getProjectAndAssociatedPromoterDetails",
        "status": "observed",
    },
    "spocs": {
        "path": f"{_PUBLIC_VIEW_PREFIX}/getProjectSpocMapping",
        "status": "observed",
    },
    "sro_details": {
        "path": f"{_PUBLIC_VIEW_PREFIX}/getProjectSroDetails",
        "status": "observed",
    },
    "past_experiences": {
        "path": "/api/maha-rera-promoter-management-service/promoter/getPromoterPastExpProject",
        "status": "observed",
    },
    "documents": {
        "path": f"{_PUBLIC_VIEW_PREFIX}/getUploadedDocuments",
        "status": "observed",
    },
    "complaints": {
        "path": f"{_PUBLIC_VIEW_PREFIX}/getComplaintDetailsByProjectId",
        "status": "observed",
    },
    "appeals": {
        "path": "/api/maha-rera-appeal-service/reatappeal/getAppealDetailsPublicView",
        "status": "observed",
    },
}

# Order categories should appear in the PDF: core identity first, "problem"
# categories (complaints/appeals) last.
CATEGORY_ORDER = [
    "projects",
    "spocs",
    "professionals",
    "partners",
    "past_experiences",
    "sro_details",
    "documents",
    "complaints",
    "appeals",
]

OUTPUT_ROOT = "output"
REQUEST_TIMEOUT = 30
SEARCH_TIMEOUT_MS = 30000

# Cap on how many of a promoter's registered projects get individually
# fetched (projects/complaints/appeals) when building their portfolio --
# never applied silently, see promoter_portfolio.py's `truncated` field.
PROMOTER_PROJECT_LIMIT = 25

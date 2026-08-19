"""
TG-RERA acquisition adapter -- a thin wrapper around ts_rera_client.py.

ts_rera_client.py is NOT modified by this. It predates the seam, works
standalone (`python ts_rera_client.py "constella"`), and its shape is
dictated by the portal rather than by us. This module adapts at the
boundary: it catches that module's own exceptions and re-raises the seam's,
and it maps its flat parse into the category_data contract.

WHAT MAKES TELANGANA DIFFERENT, AND WHY IT DECLARES NO CAPABILITIES
-------------------------------------------------------------------
Every other adapter can be handed a registration number and go. This one
cannot, and the reasons are portal facts, not gaps in the code:

  * TS-RERA CAPTCHA-GATES THE SEARCH ITSELF. There is no token-free lookup
    and no resolve/auth split to mirror -- a human solves one CAPTCHA and
    the search and fetch happen behind it. Hence no CAP_SEPARATE_AUTH.

  * THE PUBLIC RECORD DOES NOT CARRY ITS OWN REGISTRATION NUMBER. Confirmed
    in our own capture: output/CONSTELLA_TS/raw/ts_rera_project.json has
    official_ts_rera_registration_certificate_number = null, with a gap note
    explaining the number lives behind a separate CAPTCHA-gated "View
    Certificate" link. Search is BY PROJECT NAME. Hence no
    CAP_LOOKUP_BY_REG_NO -- and hence this adapter must DERIVE a storage key
    and say so, rather than pretend the authority issued one.

  * ONE PAGE, NOT NINE ENDPOINTS. A project's entire public record is a
    single server-rendered "PrintPreview". No category API, no document
    library, no complaint or appeal register, no promoter search.

So category_data is mostly None and documents_manifest is empty -- and every
one of those absences is stated in `notes` rather than left to look like a
clean check. That distinction is the whole point of the capability system:
"this authority does not publish it" must never render the same as "we
checked and found nothing".

TESTABILITY
-----------
acquire() needs a human and a browser, so the mapping is factored out into
map_detail_to_category_data(), a PURE function. test_telangana_adapter.py
replays the real CONSTELLA capture through it with no browser and no CAPTCHA,
which is the only way this adapter can be regression-tested at all.
"""

import json
import os
import re

import ts_rera_client

from .base import AcquisitionResult, StateResolutionError, storage_key
from .telangana import PROFILE

# Absences that are properties of the AUTHORITY, not of a given project.
# Lifted from the CONSTELLA Charter's own methodology note, which was
# written and human-reviewed against rules.md at the time.
_AUTHORITY_NOTES = (
    "Telangana RERA's public guest view exposes only the promoter's own submitted "
    "application record, not a downloadable document library the way MahaRERA does -- "
    "so no title report, sanctioned layout plan, bank NOC or professional-team "
    "certificate was available for review.",
    "TG-RERA publishes no public, name-searchable complaint or appeal register, so the "
    "absence of complaints in this Charter means 'not published by this authority', "
    "not 'none exist'.",
    "TG-RERA's public record does not display the project's own registration/certificate "
    "number -- it sits behind a separate CAPTCHA-gated 'View Certificate' link. The key "
    "this run is filed under was assigned by this pipeline, not issued by the authority.",
)


def _slug(value: str) -> str:
    """A stable, readable key from a project name.

    TS-RERA gives us no registration number to key on, so the output folder
    has to be derived. CONSTELLA's one-off run used the hand-written literal
    "CONSTELLA_TS"; this formalises that shape so two runs of the same
    project land in the same folder instead of accumulating duplicates."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (value or "").strip()).strip("_")
    return (cleaned.upper()[:48] or "UNKNOWN") + "_TG"


def map_detail_to_category_data(detail: dict) -> dict:
    """The flat PrintPreview parse, mapped onto the category_data contract.

    PURE -- no network, no browser. This is what makes the adapter testable
    against the saved CONSTELLA capture.

    Categories TG-RERA does not publish are None, matching api_client's own
    convention for a category that yielded nothing. They are additionally
    listed in AcquisitionResult.categories_not_published so the run summary
    can say "not published by TG-RERA" instead of "FAILED".
    """
    detail = detail or {}

    promoter = {
        "promoterName": detail.get("promoter_org_name") or "",
        "organizationType": detail.get("organization_type"),
        "panNumber": detail.get("promoter_pan"),
        "gstin": detail.get("gstin"),
        # TS-RERA's members table is the partner/director list.
        "members": detail.get("members") or [],
        "landOwnerInvestorPromoters": detail.get("land_owner_investor_promoters") or [],
    }

    project = {
        "projectName": detail.get("project_name"),
        "projectStatus": detail.get("project_status"),
        "projectType": detail.get("project_type"),
        "authorityName": detail.get("authority_name"),
        "planApprovalNumber": detail.get("plan_approval_number"),
        "approvedDate": detail.get("approved_date"),
        "proposedDateOfCompletion": detail.get("proposed_date_of_completion"),
        "litigationsRelatedToProject": detail.get("litigations_related_to_project"),
        "totalAreaSqmt": detail.get("total_area_sqmt"),
        "netAreaSqmt": detail.get("net_area_sqmt"),
        "approvedBuiltUpAreaSqmt": detail.get("approved_built_up_area_sqmt"),
        "mortgageAreaSqmt": detail.get("mortgage_area_sqmt"),
        "totalBuildingUnitsApprovedPlan": detail.get("total_building_units_approved_plan"),
        "collectionBankName": detail.get("collection_bank_name"),
        "collectionIfsc": detail.get("collection_ifsc"),
    }

    return {
        "projects": project,
        "partners": {"promoterDetails": promoter},
        # TG-RERA names no architect/engineer/CA on the public record.
        "professionals": None,
        "spocs": None,
        "sro_details": None,
        "past_experiences": None,
        "documents": None,
        "complaints": None,
        "appeals": None,
    }


def project_notes(detail: dict) -> list:
    """Per-PROJECT observations worth carrying into the document, as opposed
    to the per-authority absences in _AUTHORITY_NOTES.

    Kept separate because these are findings about this project, and a
    reader should be able to tell a finding from a limitation."""
    detail = detail or {}
    notes = []

    # Section 4(2)(l)(D) of the central RERA Act requires the 70% separate
    # account in substance. TS-RERA's form has slots for the 70/30 split and
    # CONSTELLA left both blank while declaring only the 100% collection
    # account -- a real compliance-transparency gap, not a scrape failure.
    raw = detail.get("raw_text") or ""
    if detail.get("collection_bank_name") and "Separate Account" in raw:
        if not re.search(r"Separate Account of the Project \(70%\)\nBank Name\n\S", raw):
            notes.append(
                "The project's 70% Separate Account and 30% Transaction Account are both "
                "left blank on the TG-RERA record; only the 100% collection account is "
                "declared. Section 4(2)(l)(D) of the RERA Act, 2016 requires the separate "
                "account in substance."
            )

    if detail.get("has_zero_progress_signal"):
        notes.append(
            "The TG-RERA record reports 0% progress against at least one work item."
        )

    mortgage = (detail.get("mortgage_area_sqmt") or "").strip()
    if mortgage and mortgage not in ("0", "0.0", "0.00"):
        notes.append(
            f"A mortgage area of {mortgage} sqmt is declared on the TG-RERA record, but the "
            f"record names no lender -- TG-RERA does not publish one."
        )

    if str(detail.get("litigations_related_to_project", "")).strip().lower() in ("no", "false"):
        notes.append(
            "The promoter declares no litigation related to this project. That is the "
            "promoter's own declaration on their application, not an independent check."
        )

    return notes


class TelanganaAdapter:
    """StateAdapter for TG-RERA.

    Declares no capabilities at all -- and that is a COMPLETE, valid adapter,
    not a stub. See states/base.py: a state that cannot do something omits
    the capability and returns the empty value with an honest note."""

    profile = PROFILE

    def acquire(self, query, ctx):
        if not query or not query.strip():
            raise StateResolutionError("TG-RERA search needs a project name.")

        ctx.reporter.warn(
            "TG-RERA CAPTCHA-gates its own search. A visible browser will open -- solve "
            "the CAPTCHA and click Search; this run will continue by itself afterwards."
        )
        try:
            found = ts_rera_client.search_and_fetch(
                query.strip(), timeout_seconds=ctx.captcha_timeout
            )
        except ts_rera_client.TSReraNotFoundError as e:
            raise StateResolutionError(str(e)) from e
        except (ts_rera_client.TSReraTimeoutError, ts_rera_client.TSReraBrowserClosedError) as e:
            # Wrapped, not subclassed -- ts_rera_client stays usable standalone.
            raise StateResolutionError(
                f"TG-RERA search did not complete ({e}). The CAPTCHA must be solved by hand."
            ) from e

        candidates = found.get("candidates") or []
        detail = found.get("detail")

        if not detail:
            if not candidates:
                raise StateResolutionError(f"No TG-RERA project found matching '{query}'.")
            index = ctx.reporter.choose(
                f"{len(candidates)} TG-RERA projects match '{query}'. Which one?",
                [f"{c.get('project_name', '')} | {c.get('promoter_name', '')}"
                 f" | {c.get('last_modified', '')}" for c in candidates],
            )
            if index is None:
                raise StateResolutionError(
                    f"{len(candidates)} TG-RERA projects match '{query}' and the choice could "
                    f"not be made non-interactively. Re-run with a more specific project name."
                )
            href = candidates[index].get("view_href")
            if not href:
                raise StateResolutionError(
                    "That TG-RERA result carries no PrintPreview link, so its record cannot "
                    "be opened."
                )
            # The `q` token in a PrintPreview URL is session-scoped, so this
            # only works within the same short-lived session the search ran in.
            detail = ts_rera_client.fetch_detail_by_url(href)

        project_name = detail.get("project_name") or query.strip()
        reg_no = storage_key(_slug(project_name))
        ctx.reporter.ok(f"Resolved: {project_name} (filed under {reg_no})")

        prior = ctx.prior or {}
        if ctx.on_resolved is not None:
            prior = ctx.on_resolved(reg_no) or {}

        raw_dir = os.path.join(ctx.output_dir, reg_no, "raw")
        os.makedirs(raw_dir, exist_ok=True)

        category_data = map_detail_to_category_data(detail)
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        # The whole parse, including raw_text -- the record keeps what the
        # category mapping drops.
        with open(os.path.join(raw_dir, "ts_rera_project.json"), "w", encoding="utf-8") as f:
            json.dump(detail, f, indent=2, ensure_ascii=False)

        notes = list(_AUTHORITY_NOTES) + project_notes(detail)
        for note in notes:
            ctx.reporter.info(note)

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            # None: the authority genuinely issued no number we can see.
            registration_number=None,
            project_id=None,
            detail_url=None,
            category_data=category_data,
            documents_manifest=[],
            documents_dir=None,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=detail.get("promoter_org_name"),
            promoter_portfolio=None,
            raw_record=detail,
            auth_source="fresh_browser",
            categories_not_published={
                "professionals", "spocs", "sro_details",
                "past_experiences", "documents", "complaints", "appeals",
            },
            notes=notes,
        )


ADAPTER = TelanganaAdapter()

"""
StateAdapter for Delhi / Delhi-RERA.

See states/delhi.py for the register's size and why that size is the single
most important thing this adapter reports.

THE WHOLE REGISTER IS ONE GET, AND THAT IS THE WHOLE ADAPTER. 130 projects,
no CAPTCHA, no login, no pagination -- district, project name, PROMOTER
name, registration number, validity and project type, all in the index. So
resolving a registration number is a lookup, not a search, and a promoter's
other Delhi projects are a local join rather than a second request.

THERE IS NO PER-PROJECT RECORD TO OPEN, AND THAT IS NOT AN OVERSIGHT. The
register's own "View Details" control is inert:

    <a class="btn view-button" href="javascript:void(0);"
       id="modalOpenerButton" title="View Details">View</a>

Verified against the served page: no href, no data- attribute, no ajax call,
no `url:` literal, and no detail route referenced anywhere in it. Six
plausible detail routes were probed and every one returned 404. So this
adapter returns what the index states and declares everything else NOT
PUBLISHED, rather than shipping a capability it cannot deliver -- the
mistake states/adapter_gujarat.py made once with promoter portfolios.

WHAT THAT MEANS FOR A READER, and why the notes below are the real product:
a Delhi project's record here is an identity and a validity date. It carries
no land details, no escrow account, no professionals of record and no
documents -- not because they were not fetched, but because this interface
does not serve them. Any of those appearing blank in a Charter is the
authority's limit, never a finding about the promoter.
"""

import json
import os
import re

import requests
from bs4 import BeautifulSoup

from .base import (
    AcquisitionResult,
    StateFetchError,
    StateResolutionError,
    fetch_with_retry,
    storage_key,
)
from .delhi import ORDER_REGISTER, PROFILE, STATE_INDEX

_TIMEOUT = 90
_UA = "RERA-Scrapper-DueDiligence/1.0 (research tool, low-volume)"

# "RR TEXKNIT LLP (Other than Individual)" -- the parenthetical is the
# authority's own applicant classification, not part of the name. Worth
# keeping separately: an individual promoter and a company are different
# diligence subjects, and it is the only such signal the index carries.
_APPLICANT_KIND_RE = re.compile(r"\s*\((Individual|Other than Individual)\)\s*$", re.I)

_AUTHORITY_NOTES = [
    "Delhi-RERA's public register contains 130 projects in total, for the whole National "
    "Capital Territory, across 2018 to 2026 -- against roughly 55,000 on MahaRERA. Delhi's "
    "market is overwhelmingly resale and plotted development falling outside the "
    "registration thresholds, and the authority has registered 712 agents against those 130 "
    "projects. A promoter with genuine Delhi activity may therefore have no Delhi-RERA "
    "record at all, and the absence of one is close to worthless as evidence.",
    "Delhi-RERA publishes no per-project record through this interface: the register's own "
    "'View Details' control is inert and every detail route probed returns nothing. So this "
    "project's land details, escrow accounts, professionals of record and filed documents "
    "were not merely unfetched -- they are not published. None of them may be read as absent "
    "from the promoter's filing.",
]


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    return s


def _get(session, url, what="page"):
    def _fetch():
        response = session.get(url, timeout=_TIMEOUT)
        response.raise_for_status()
        return response.text

    return fetch_with_retry(_fetch, what=what)


def split_applicant_kind(promoter):
    """('RR TEXKNIT LLP', 'Other than Individual') from the index's own cell.

    Pure so it is testable against the real string without a fetch.
    """
    promoter = " ".join(str(promoter or "").split())
    match = _APPLICANT_KIND_RE.search(promoter)
    if not match:
        return promoter, ""
    return _APPLICANT_KIND_RE.sub("", promoter).strip(), match.group(1)


def _rows_of(table):
    """(cell texts, cell elements) per row.

    The elements are kept because two things this register publishes live in
    the markup rather than the text: the clean registration number in a
    `data-diary-no` attribute, and each judgement's PDF link.
    """
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]
        if any(texts):
            rows.append((texts, cells, tr))
    return rows


def strip_disclaimer_marker(value):
    """'DLRERA2025P0003 *' -> 'DLRERA2025P0003'.

    THE REGISTER APPENDS A FOOTNOTE MARKER INTO THE CELL. One of the 130
    rows carries `<label style="color:red" title="view disclaimer">*</label>`
    inside both its registration-number cell and its project-name cell, so
    the cell text reads "DLRERA2025P0003 *" and "Good Earth Capital Crest *".

    Left in place that number matches nothing: not the DL profile's own
    reg-no pattern, and not a reader pasting the number as the authority
    issued it -- so this one project was unresolvable by its own
    registration number, and invisible to any portfolio or sweep match. It
    is the same species as HARERA's "Lapsed Project" flag concatenating onto
    a certificate number, which that adapter already strips.

    The marker itself is not discarded; `has_disclaimer` carries it, because
    the authority flagging a registration is diligence material.
    """
    return re.sub(r"\s*\*+\s*$", "", " ".join(str(value or "").split())).strip()


def parse_state_index(html):
    """The whole register as [{reg_no, project_name, promoter_name, ...}].

    THE TABLE IS FOUND BY ITS HEADER, NEVER BY POSITION. The page serves two
    131-row tables whose headers differ only in the middle: the register
    proper carries "Promoter's Name", and the quarterly-updates table
    carries "Quarter Name" instead. Taking tables[0] would work today and
    break silently the moment the page reorders them -- the lesson the
    JHARERA and K-RERA adapters both had to learn.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if not rows:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        if "registration number" not in joined or "promoter" not in joined:
            continue

        def _column(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        columns = {
            "district": _column("district"),
            "project_name": _column("project", "name"),
            "promoter": _column("promoter"),
            "reg_no": _column("registration number"),
            "valid_upto": _column("valid"),
            "project_type": _column("type"),
        }
        if columns["reg_no"] is None:
            continue

        out = []
        for texts, cells, row_element in rows[1:]:
            def _cell(key):
                index = columns.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            raw_reg_no = _cell("reg_no")
            # The register states the number twice: once as cell text, which
            # a footnote marker can contaminate, and once as a clean
            # `data-diary-no` attribute. Prefer the attribute; fall back to
            # the stripped text.
            diary = row_element.find(attrs={"data-diary-no": True})
            reg_no = (diary["data-diary-no"].strip() if diary
                      else strip_disclaimer_marker(raw_reg_no))
            if not reg_no:
                continue
            promoter, kind = split_applicant_kind(_cell("promoter"))
            out.append({
                "reg_no": reg_no,
                "project_name": strip_disclaimer_marker(_cell("project_name")),
                "promoter_name": promoter,
                "applicant_kind": kind,
                "district": _cell("district"),
                "registration_valid_upto": _cell("valid_upto"),
                "project_type": _cell("project_type"),
                # The authority attached a disclaimer to this registration.
                "has_disclaimer": "*" in raw_reg_no,
            })
        if out:
            return out
    return []


_INDEX_CACHE = []


def _index(session=None):
    if not _INDEX_CACHE:
        _INDEX_CACHE.extend(parse_state_index(
            _get(session or _session(), STATE_INDEX, what="Delhi-RERA register")
        ))
    return _INDEX_CACHE


def parse_order_register(html):
    """Delhi-RERA's complaint register, as [{complaint_no, complainant,
    respondent, decided_on}].

    It NAMES BOTH PARTIES in their own columns, which is what makes it
    searchable by promoter -- most authorities in this pipeline publish
    orders keyed only by case number. A complaint is filed against the
    promoter, so the RESPONDENT is the side to match on.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if not rows:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        if "respondent" not in joined:
            continue

        def _column(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        idx = {
            "complaint_no": _column("complaint"),
            "complainant": _column("complainant"),
            "respondent": _column("respondent"),
            "decided_on": _column("decision") or _column("date"),
        }
        out = []
        for texts, cells, row_element in rows[1:]:
            def _cell(key):
                index = idx.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            if not _cell("respondent").strip():
                continue
            # ONE ROW IS ONE ORDER, NOT ONE COMPLAINT. 624 rows carry 539
            # distinct complaint numbers: a complaint decided more than once
            # gets a row per judgement, differing only in the serial number
            # and in the PDF each one links. Counting rows as complaints
            # overstates a promoter's complaint history, and dropping the
            # link throws away the only evidence of the order itself.
            order_url = ""
            for anchor in row_element.find_all("a", href=True):
                order_url = anchor["href"]
                break
            out.append({
                "complaint_no": _cell("complaint_no"),
                "complainant": _cell("complainant"),
                "respondent": _cell("respondent"),
                "decided_on": _cell("decided_on"),
                "order_url": order_url,
            })
        if out:
            return out
    return []


def distinct_complaints(rows):
    """How many COMPLAINTS a set of order rows represents.

    ONE ROW IS ONE ORDER, NOT ONE COMPLAINT, and the register publishes
    every interim order. Complaint 30/2020 -- one complainant, one
    respondent -- occupies THIRTY-FOUR rows differing only by decision date.
    Counting rows would report that promoter as having 34 complaints.

    But the complaint number alone is not the key either. Across the whole
    register 624 rows carry 539 distinct (number + parties + date)
    combinations, 68 distinct (number + parties), and only 51 distinct
    NUMBERS -- so numbers are reused between unrelated cases, and collapsing
    on the number alone would merge different people's complaints into one
    and UNDER-report. The parties are what separate them.
    """
    keys = set()
    for row in rows:
        number = " ".join((row.get("complaint_no") or "").split())
        if not number:
            continue
        keys.add((
            number.casefold(),
            " ".join((row.get("complainant") or "").split()).casefold(),
            " ".join((row.get("respondent") or "").split()).casefold(),
        ))
    return keys


_ORDER_CACHE = []


def fetch_order_register(fetcher=None):
    """The whole complaint register, cached for the process."""
    if _ORDER_CACHE and fetcher is None:
        return _ORDER_CACHE
    html = fetcher() if fetcher is not None else _get(
        _session(), ORDER_REGISTER, what="Delhi-RERA complaint register"
    )
    parsed = parse_order_register(html)
    if fetcher is None:
        _ORDER_CACHE.extend(parsed)
    return parsed


def search_orders_by_promoter(name, fetcher=None):
    """Complaints whose RESPONDENT names `name`.

    A complaint is filed against the promoter, so the respondent is the
    promoter. Matching is a normalised substring, so every hit is a
    CANDIDATE rather than a confirmed order against this entity -- the
    caller labels them that way.
    """
    needle = " ".join(str(name or "").split()).casefold()
    if not needle:
        return []
    return [row for row in fetch_order_register(fetcher)
            if needle in " ".join((row.get("respondent") or "").split()).casefold()]


class _NullReporter:
    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def ok(self, *a, **k): pass
    def choose(self, *a, **k): return None


def search_promoter_projects(name, reporter=None):
    """Projects in the Delhi register under a promoter matching `name`.

    One GET for the whole state, then a local substring match -- the same
    shape K-RERA's sweep search has, over 130 rows instead of 9,888.
    """
    needle = " ".join(str(name or "").split()).casefold()
    if not needle:
        return []
    try:
        index = _index()
    except StateFetchError:
        return []
    return [
        {"reg_no": entry["reg_no"], "project_name": entry["project_name"],
         "promoter_name": entry["promoter_name"],
         # Carried so a caller can open the project -- though on this
         # authority opening it adds nothing, since there is no per-project
         # record. fetch_project_summary says so rather than staying silent.
         "project_id": entry["reg_no"]}
        for entry in index
        if needle in " ".join((entry["promoter_name"] or "").split()).casefold()
    ]


def fetch_project_summary(project_ref, reporter=None):
    """What the register states about ONE Delhi project -- which is all
    there is.

    Deliberately returns `opened: True` with the index fields rather than
    pretending a detail page was read. The promoter name matters most: it is
    what group_sweep.enrich_projects confirms or refutes a candidate on, and
    Delhi's index carries it, so a Delhi hit CAN be confirmed even though
    nothing deeper is reachable.
    """
    reporter = reporter or _NullReporter()
    needle = str(project_ref or "").strip().casefold()
    if not needle:
        return {"opened": False, "note": "No Delhi-RERA registration number was carried."}
    try:
        index = _index()
    except StateFetchError as e:
        return {"opened": False,
                "note": f"Delhi-RERA's register could not be read ({type(e).__name__})."}

    entry = next((e for e in index if e["reg_no"].casefold() == needle), None)
    if entry is None:
        return {"opened": False,
                "note": (f"'{project_ref}' is not in Delhi-RERA's register of {len(index)} "
                         f"projects. Note that issued numbers have gaps, so this is not proof "
                         f"the registration never existed.")}
    return {
        "opened": True,
        "promoter_name": entry["promoter_name"],
        "applicant_kind": entry["applicant_kind"],
        "project_name": entry["project_name"],
        "reg_no": entry["reg_no"],
        "district": entry["district"],
        "project_type": entry["project_type"],
        "registration_valid_upto": entry["registration_valid_upto"],
        "notes": [
            "Delhi-RERA publishes no per-project record, so this is everything its register "
            "states about the project. Nothing further was withheld or missed."
        ],
    }


class DelhiAdapter:
    """StateAdapter for Delhi-RERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        session = _session()

        ctx.reporter.info("Fetching the Delhi-RERA register (all districts, one request)...")
        index = parse_state_index(_get(session, STATE_INDEX, what="Delhi-RERA register"))
        if not index:
            raise StateFetchError(
                "Delhi-RERA's register returned no projects at all this run, which means the "
                "register could not be read rather than that Delhi has no registered projects."
            )
        ctx.reporter.ok(f"{len(index)} Delhi-RERA projects indexed.")

        needle = query.strip().casefold()
        exact = [e for e in index if e["reg_no"].casefold() == needle]
        matches = exact or [e for e in index if needle in e["project_name"].casefold()]
        if not matches:
            raise StateResolutionError(
                f"No Delhi-RERA project found matching '{query}' in a register of "
                f"{len(index)} projects. Delhi's register is small -- 130 projects for the "
                f"whole NCT -- so an absence here is weak evidence: a genuine Delhi project "
                f"may fall below the registration threshold entirely."
            )
        chosen = matches[0]
        if len(matches) > 1:
            ctx.reporter.warn(
                f"{len(matches)} Delhi-RERA projects matched {query!r}; using "
                f"{chosen['reg_no']} ({chosen['project_name']})."
            )

        registration_number = chosen["reg_no"]
        reg_no = storage_key(registration_number)
        ctx.reporter.ok(f"Resolved: {registration_number} | {chosen['project_name']}")

        project_out_dir = os.path.join(ctx.output_dir, reg_no)
        raw_dir = os.path.join(project_out_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        if ctx.on_resolved is not None:
            ctx.on_resolved(reg_no)
            os.makedirs(raw_dir, exist_ok=True)

        promoter_name = chosen["promoter_name"]
        complaints, complaint_notes = self._complaints(promoter_name, ctx)

        disclaimer_notes = []
        if chosen.get("has_disclaimer"):
            ctx.reporter.warn(
                "Delhi-RERA has marked this registration with a disclaimer marker."
            )
            disclaimer_notes.append(
                "Delhi-RERA prints a red disclaimer marker against this registration on its own "
                "register, beside both the registration number and the project name. The "
                "authority does not publish what the marker means for a given project -- the "
                "only disclaimer text on the page is a site-wide notice about data migration -- "
                "so it is recorded here as stated and its meaning should be confirmed with the "
                "authority. It is not being read as a finding either way."
            )

        category_data = {
            "projects": {
                "projectName": chosen["project_name"],
                "projectRegistartionNo": registration_number,
                "district": chosen["district"],
                "projectTypeName": chosen["project_type"],
                "registrationValidUpto": chosen["registration_valid_upto"],
            },
            "partners": {"promoterDetails": {
                "promoterName": promoter_name,
                "applicantKind": chosen["applicant_kind"],
            }},
            # Not published by this authority -- None, never {} or 0, so
            # nothing downstream can read an absence as a clean record.
            "professionals": None,
            "spocs": None,
            "sro_details": None,
            "past_experiences": None,
            "documents": None,
            "complaints": complaints,
            "appeals": None,
        }
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            registration_number=registration_number,
            project_id=registration_number,
            detail_url=STATE_INDEX,
            category_data=category_data,
            documents_manifest=[],
            documents_dir=None,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=promoter_name,
            promoter_portfolio=self._promoter_portfolio(index, chosen, ctx),
            raw_record=chosen,
            auth_source="none",
            categories_not_published={
                "professionals", "spocs", "sro_details", "past_experiences",
                "documents", "appeals",
            },
            notes=list(_AUTHORITY_NOTES) + disclaimer_notes + complaint_notes,
        )

    # -- complaints --------------------------------------------------------
    def _complaints(self, promoter_name, ctx):
        """Delhi-RERA's complaint register, matched on RESPONDENT.

        A count that could not be read is None, never 0. The register names
        parties, so a match is a POSSIBLE complaint against this promoter
        rather than a confirmed one -- names repeat.
        """
        try:
            rows = search_orders_by_promoter(promoter_name)
        except Exception:
            return ({"total_complaints_count": None,
                     "source": "Delhi-RERA complaint register"},
                    ["Delhi-RERA's complaint register could not be read this run, so this "
                     "promoter's complaint history is UNKNOWN. It must not be read as zero."])
        complaints = distinct_complaints(rows)
        if rows:
            ctx.reporter.warn(
                f"Delhi-RERA's complaint register names {len(complaints)} matching "
                f"complaint(s) against {len(rows)} published order(s)."
            )
            note = (
                f"{len(complaints)} complaint(s) on Delhi-RERA's public register name a "
                f"respondent matching this promoter's name, carrying {len(rows)} published "
                f"order(s) between them. The register is keyed on names rather than any "
                f"identifier, so these are possible matches to confirm, not confirmed "
                f"complaints against this entity."
            )
        else:
            ctx.reporter.ok("Delhi-RERA's complaint register names no matching respondent.")
            note = None
        return ({"total_complaints_count": len(complaints),
                 "total_orders_published": len(rows),
                 "source": "Delhi-RERA complaint register (name match on respondent)",
                 "matches": rows},
                [note] if note else [])

    # -- portfolio ---------------------------------------------------------
    def _promoter_portfolio(self, index, chosen, ctx):
        """Other Delhi projects under the same promoter.

        An EXACT normalised name match, not a substring: the register is
        small and a loose match here would fold an unrelated promoter's
        projects into this one's track record.
        """
        target = " ".join((chosen["promoter_name"] or "").split()).casefold()
        if not target:
            return None
        others = [
            {"reg_no": e["reg_no"], "project_name": e["project_name"],
             "district": e["district"], "project_type": e["project_type"],
             "registration_valid_upto": e["registration_valid_upto"]}
            for e in index
            if " ".join((e["promoter_name"] or "").split()).casefold() == target
        ]
        ctx.reporter.info(f"Delhi-RERA portfolio: {len(others)} project(s) under this promoter.")
        return {
            "promoter_name": chosen["promoter_name"],
            "projects": others,
            "totals": {"total_projects": len(others)},
            "source": "Delhi-RERA registered-projects register",
            "notes": [
                "Matched on the promoter's name exactly as the register prints it. Delhi-RERA "
                "publishes no promoter identifier, so a promoter who filed under a differently "
                "spelled name would not be joined to these."
            ],
        }


ADAPTER = DelhiAdapter()

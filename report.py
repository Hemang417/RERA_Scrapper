"""
Builds the single structured summary PDF from fetched category data.
Uses ReportLab (pure-Python, no native deps -- deploys cleanly if this is
ever hosted as a service).
"""

from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)

import config

_STYLES = getSampleStyleSheet()
_TITLE_STYLE = ParagraphStyle("TitleBig", parent=_STYLES["Title"], fontSize=22, spaceAfter=6)
_SECTION_STYLE = ParagraphStyle("Section", parent=_STYLES["Heading2"], spaceBefore=14, spaceAfter=8)
_SUBSECTION_STYLE = ParagraphStyle("Subsection", parent=_STYLES["Heading3"], spaceBefore=10, spaceAfter=4)
_BODY_STYLE = _STYLES["BodyText"]
_NOTE_STYLE = ParagraphStyle("Note", parent=_STYLES["BodyText"], textColor=colors.HexColor("#8a6d00"))
_SOURCE_STYLE = ParagraphStyle("Source", parent=_BODY_STYLE, fontSize=8, textColor=colors.HexColor("#555555"))


def _esc(value) -> str:
    """Escapes text before it goes into a ReportLab Paragraph, which parses
    a mini XML-like markup -- an unescaped '&' (common in real company
    names, e.g. "R & B Constructions") or stray '<'/'>' would otherwise
    raise a parse error and abort the whole PDF build."""
    return _xml_escape(str(value))

_CATEGORY_LABELS = {
    "projects": "Project Details",
    "professionals": "Professionals",
    "partners": "Partners",
    "spocs": "SPOCs (Single Point of Contact)",
    "sro_details": "SRO Details",
    "past_experiences": "Past Experiences",
    "documents": "Documents",
    "complaints": "Complaints",
    "appeals": "Appeals",
}

_MAX_TABLE_COLS = 6

_AUTH_SOURCE_LABELS = {
    "explicit": "manually supplied token",
    "cached": "reused cached session",
    "fresh_browser": "freshly solved CAPTCHA session",
    "none": "no session (free categories only)",
}


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.drawString(2 * cm, 1.2 * cm, f"MahaRERA report -- {doc._reg_no}")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


_LONG_VALUE_THRESHOLD = 300


def _kv_table(data: dict) -> list:
    """Renders short fields as a compact Field/Value table. Fields whose
    value is unusually long (free-text complaint/appeal details, etc.) are
    pulled out into their own paragraph below the table instead -- a fixed
    5x11cm table cell can't split across a page break, so a long value stuck
    in one raises ReportLab's LayoutError once it's taller than one page."""
    short_items = {k: v for k, v in data.items() if len(_esc(v)) <= _LONG_VALUE_THRESHOLD}
    long_items = {k: v for k, v in data.items() if len(_esc(v)) > _LONG_VALUE_THRESHOLD}

    flowables = []
    if short_items:
        rows = [[Paragraph(_esc(k), _BODY_STYLE), Paragraph(_esc(v), _BODY_STYLE)] for k, v in short_items.items()]
        table = Table(rows, colWidths=[5 * cm, 11 * cm])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                ]
            )
        )
        flowables.append(table)
    for k, v in long_items.items():
        flowables.append(Spacer(1, 4))
        flowables.append(Paragraph(f"<b>{_esc(k)}</b>", _BODY_STYLE))
        flowables.append(Paragraph(_esc(v), _BODY_STYLE))
    return flowables


def _list_table(records: list) -> list:
    """Returns a list of flowables (a header table plus, for records with too
    many fields, a key-value sub-block per row)."""
    flowables = []
    all_keys = []
    for rec in records:
        if isinstance(rec, dict):
            for k in rec.keys():
                if k not in all_keys:
                    all_keys.append(k)

    if not all_keys:
        # list of scalars, not dicts
        rows = [[Paragraph(_esc(v), _BODY_STYLE)] for v in records]
        table = Table(rows, colWidths=[16 * cm])
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
        flowables.append(table)
        return flowables

    any_long_value = any(
        isinstance(rec, dict) and len(_esc(rec.get(k, ""))) > _LONG_VALUE_THRESHOLD
        for rec in records
        for k in all_keys
    )

    if len(all_keys) <= _MAX_TABLE_COLS and not any_long_value:
        header = [Paragraph(f"<b>{_esc(k)}</b>", _BODY_STYLE) for k in all_keys]
        rows = [header]
        for rec in records:
            rows.append([Paragraph(_esc(rec.get(k, "")), _BODY_STYLE) for k in all_keys])
        col_width = 16 * cm / len(all_keys)
        table = Table(rows, colWidths=[col_width] * len(all_keys))
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe7f2")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                ]
            )
        )
        flowables.append(table)
    else:
        # Too many fields for a readable wide table, or at least one field has
        # an unusually long value that couldn't survive being trapped in a
        # fixed-width table cell -- render one key-value block per record
        # instead (_kv_table already pulls long values out into paragraphs).
        for i, rec in enumerate(records, start=1):
            flowables.append(Paragraph(f"<b>Record {i}</b>", _BODY_STYLE))
            flowables.extend(_kv_table(rec))
            flowables.append(Spacer(1, 6))

    return flowables


def _render_category_section(category: str, data, failed_categories: set) -> list:
    flowables = [Paragraph(_CATEGORY_LABELS.get(category, category), _SECTION_STYLE)]

    if category in failed_categories:
        flowables.append(
            Paragraph(
                "Could not be retrieved -- this category needs a guest access token "
                "(pass --token; see main.py --help) or its endpoint failed. Run with "
                "--verify to check.",
                _NOTE_STYLE,
            )
        )
        return flowables

    if not data:
        flowables.append(Paragraph("None found.", _BODY_STYLE))
        return flowables

    if isinstance(data, dict):
        non_empty = {k: v for k, v in data.items() if v not in (None, "", [], {})}
        if non_empty:
            flowables.extend(_kv_table(non_empty))
        else:
            flowables.append(Paragraph("None found.", _BODY_STYLE))
    elif isinstance(data, list):
        flowables.extend(_list_table(data))
    else:
        flowables.append(Paragraph(_esc(data), _BODY_STYLE))

    return flowables


def _render_documents_section(manifest: list) -> list:
    flowables = [Paragraph(_CATEGORY_LABELS["documents"], _SECTION_STYLE)]

    if not manifest:
        flowables.append(Paragraph("No documents found or documents endpoint unconfirmed.", _BODY_STYLE))
        return flowables

    header = ["Document", "Saved as", "Status"]
    rows = [header]
    for entry in manifest:
        rows.append(
            [
                Paragraph(_esc(entry.get("label", "")), _BODY_STYLE),
                Paragraph(_esc(entry.get("saved_filename") or "--"), _BODY_STYLE),
                Paragraph(_esc(entry.get("status", "")), _BODY_STYLE),
            ]
        )
    table = Table(rows, colWidths=[7 * cm, 6 * cm, 3 * cm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe7f2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )
    flowables.append(table)
    flowables.append(Spacer(1, 4))
    flowables.append(Paragraph("Original files are saved in the accompanying documents/ folder.", _NOTE_STYLE))
    return flowables


_RESEARCH_SECTION_LABELS = {
    "macro_market": "Macro Market Research",
    "micro_market": "Micro Market Research (Locality)",
    "promoter_external": "Promoter External Profile",
}


def _render_research_block(label: str, block: dict | None) -> list:
    """Renders one agentic-research block (macro_market/micro_market/
    promoter_external -- see the deep_research.json schema) as: summary ->
    labeled sub-sections -> a sources table -> any unresolved gaps. Every
    field is optional and nothing here ever raises -- a block that's
    entirely absent (no agentic research pass has run yet for this project)
    renders a plain, honest note instead of a crash or fabricated content."""
    flowables = [Paragraph(label, _SUBSECTION_STYLE)]

    if not block or not isinstance(block, dict):
        flowables.append(
            Paragraph(
                "Not generated this run -- no agentic research pass has produced this "
                "section yet. Run the deep-research step and rebuild the report (see "
                "finalize_report.py) to populate it.",
                _NOTE_STYLE,
            )
        )
        return flowables

    summary = block.get("summary")
    if summary:
        flowables.append(Paragraph(_esc(summary), _BODY_STYLE))
        flowables.append(Spacer(1, 4))

    sections = block.get("sections")
    if isinstance(sections, list):
        for sub in sections:
            if not isinstance(sub, dict):
                continue
            heading = sub.get("heading")
            body = sub.get("body")
            if heading:
                flowables.append(Paragraph(f"<b>{_esc(heading)}</b>", _BODY_STYLE))
            if body:
                flowables.append(Paragraph(_esc(body), _BODY_STYLE))
            flowables.append(Spacer(1, 4))

    sources = block.get("sources")
    if isinstance(sources, list) and sources:
        flowables.append(Paragraph("<b>Sources</b>", _BODY_STYLE))
        rows = [["Claim", "Source", "Accessed"]]
        for src in sources:
            if not isinstance(src, dict):
                continue
            url = src.get("url") or ""
            publisher = src.get("publisher") or url
            source_cell = f'<a href="{_esc(url)}">{_esc(publisher)}</a>' if url else _esc(publisher)
            rows.append(
                [
                    Paragraph(_esc(src.get("claim", "")), _SOURCE_STYLE),
                    Paragraph(source_cell, _SOURCE_STYLE),
                    Paragraph(_esc(src.get("accessed_date", "")), _SOURCE_STYLE),
                ]
            )
        table = Table(rows, colWidths=[8 * cm, 6 * cm, 2 * cm])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe7f2")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                ]
            )
        )
        flowables.append(table)
        flowables.append(Spacer(1, 4))

    gaps = block.get("gaps")
    if isinstance(gaps, list) and gaps:
        flowables.append(
            Paragraph("<b>Unresolved gaps</b> (multiple sources tried, couldn't confirm):", _NOTE_STYLE)
        )
        for gap in gaps:
            flowables.append(Paragraph(f"• {_esc(gap)}", _NOTE_STYLE))

    return flowables


def _promoter_projects_table(rows: list) -> Table:
    """Fixed, hand-tuned column widths rather than _list_table()'s even
    division -- a 13-char registration number wraps badly if squeezed into
    an even 1/6th share of the row width."""
    header = ["Reg. No.", "Project", "Status", "Complaints", "Appeals", "Flag"]
    col_widths = [2.9 * cm, 4.5 * cm, 2.8 * cm, 2.0 * cm, 1.8 * cm, 2.0 * cm]
    table_rows = [[Paragraph(f"<b>{h}</b>", _SOURCE_STYLE) for h in header]]
    for r in rows:
        complaints = r.get("complaint_count")
        appeals = r.get("appeal_count")
        table_rows.append(
            [
                Paragraph(_esc(r.get("reg_no") or ""), _SOURCE_STYLE),
                Paragraph(_esc(r.get("project_name") or ""), _SOURCE_STYLE),
                Paragraph(_esc(r.get("status") or ""), _SOURCE_STYLE),
                Paragraph(_esc(complaints if complaints is not None else "?"), _SOURCE_STYLE),
                Paragraph(_esc(appeals if appeals is not None else "?"), _SOURCE_STYLE),
                Paragraph("FLAGGED" if r.get("is_lapsed") else "", _SOURCE_STYLE),
            ]
        )
    table = Table(table_rows, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe7f2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _render_promoter_profile_section(promoter_portfolio: dict | None, promoter_external: dict | None) -> list:
    flowables = [Paragraph("Promoter Profile", _SECTION_STYLE)]

    if not promoter_portfolio:
        flowables.append(
            Paragraph(
                "Promoter portfolio could not be determined this run (promoter name "
                "unknown, or the RERA Promoters-tab lookup failed).",
                _NOTE_STYLE,
            )
        )
    else:
        totals = promoter_portfolio.get("totals", {})
        summary_kv = {
            "Promoter (searched)": promoter_portfolio.get("promoter_name_searched", ""),
            "Total registered projects": totals.get("total_projects", 0),
            "Total complaints (all projects)": totals.get("total_complaints", 0),
            "Total appeals (all projects)": totals.get("total_appeals", 0),
            "Projects with complaints": totals.get("projects_with_complaints", 0),
            "Projects with appeals": totals.get("projects_with_appeals", 0),
            "Lapsed / flagged projects": totals.get("lapsed_or_flagged_count", 0),
        }
        flowables.extend(_kv_table(summary_kv))
        flowables.append(Spacer(1, 6))

        for note in promoter_portfolio.get("limitations", []):
            flowables.append(Paragraph(f"• {_esc(note)}", _NOTE_STYLE))
        flowables.append(Spacer(1, 6))

        rows = promoter_portfolio.get("projects", [])
        if rows:
            flowables.append(Paragraph("<b>Registered projects</b>", _BODY_STYLE))
            flowables.append(_promoter_projects_table(rows))
        else:
            flowables.append(Paragraph("No individual project records available.", _BODY_STYLE))

    flowables.append(Spacer(1, 10))
    flowables.extend(_render_research_block(_RESEARCH_SECTION_LABELS["promoter_external"], promoter_external))
    return flowables


def _render_market_research_section(research_data: dict | None) -> list:
    flowables = [Paragraph("Market Research", _SECTION_STYLE)]
    research_data = research_data or {}
    flowables.extend(_render_research_block(_RESEARCH_SECTION_LABELS["macro_market"], research_data.get("macro_market")))
    flowables.append(Spacer(1, 10))
    flowables.extend(_render_research_block(_RESEARCH_SECTION_LABELS["micro_market"], research_data.get("micro_market")))
    return flowables


def build_pdf(
    reg_no: str,
    project_id: str,
    category_data: dict,
    documents_manifest: list,
    out_path: str,
    auth_source: str | None = None,
    promoter_portfolio: dict | None = None,
    research_data: dict | None = None,
) -> None:
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )
    doc._reg_no = reg_no

    failed_categories = {cat for cat, data in category_data.items() if data is None}
    guessed_categories = [
        cat for cat in config.CATEGORY_ORDER if config.CATEGORY_ENDPOINTS[cat]["status"] != "confirmed"
    ]

    project_info = category_data.get("projects")
    project_name = ""
    promoter_name = ""
    if isinstance(project_info, dict):
        project_name = project_info.get("projectName") or project_info.get("name") or ""
    elif isinstance(project_info, list) and project_info:
        first = project_info[0]
        if isinstance(first, dict):
            project_name = first.get("projectName") or first.get("name") or ""

    # projects.promoterName is null in practice on every sample seen -- the
    # real promoter name lives on partners.promoterDetails.promoterName
    # (confirmed live), with stray trailing whitespace to strip.
    partners_info = category_data.get("partners")
    if isinstance(partners_info, dict):
        promoter_details = partners_info.get("promoterDetails")
        if isinstance(promoter_details, dict) and promoter_details.get("promoterName"):
            promoter_name = promoter_details["promoterName"].strip()
    if not promoter_name and isinstance(project_info, dict):
        promoter_name = (project_info.get("promoterName") or project_info.get("promoter") or "").strip()

    story = []

    story.append(Spacer(1, 4 * cm))
    story.append(Paragraph("MahaRERA Project Report", _TITLE_STYLE))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(f"<b>Registration No.:</b> {reg_no}", _BODY_STYLE))
    story.append(Paragraph(f"<b>Internal Project ID:</b> {project_id}", _BODY_STYLE))
    if project_name:
        story.append(Paragraph(f"<b>Project Name:</b> {project_name}", _BODY_STYLE))
    if promoter_name:
        story.append(Paragraph(f"<b>Promoter:</b> {promoter_name}", _BODY_STYLE))
    story.append(Paragraph(f"<b>Generated:</b> {datetime.now().strftime('%d %b %Y %H:%M')}", _BODY_STYLE))
    if auth_source:
        story.append(
            Paragraph(
                f"<b>Data session:</b> {_AUTH_SOURCE_LABELS.get(auth_source, auth_source)}",
                _BODY_STYLE,
            )
        )

    if guessed_categories:
        story.append(Spacer(1, 0.5 * cm))
        story.append(
            Paragraph(
                "<b>Data reliability note:</b> the following categories used "
                "unconfirmed/guessed API endpoints at generation time: "
                + ", ".join(_CATEGORY_LABELS.get(c, c) for c in guessed_categories)
                + ". Sections that failed to fetch are marked below.",
                _NOTE_STYLE,
            )
        )

    story.append(PageBreak())

    # Project Details first, then the two new deep-research sections, then
    # the rest of the existing operational categories in their usual order
    # (documents/complaints/appeals last) -- reads like a due-diligence memo:
    # what is this, who's building it, is the market/location good, then the
    # regulatory paper trail.
    story.extend(_render_category_section("projects", category_data.get("projects"), failed_categories))
    story.append(PageBreak())

    promoter_external = (research_data or {}).get("promoter_external")
    story.extend(_render_promoter_profile_section(promoter_portfolio, promoter_external))
    story.append(PageBreak())

    story.extend(_render_market_research_section(research_data))
    story.append(PageBreak())

    for category in config.CATEGORY_ORDER:
        if category == "projects":
            continue
        if category == "documents":
            story.extend(_render_documents_section(documents_manifest))
        else:
            story.extend(_render_category_section(category, category_data.get(category), failed_categories))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)

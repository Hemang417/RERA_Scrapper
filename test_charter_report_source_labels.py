"""
Tests for two leaks in charter_report.py's citation labelling:

  * a raw code identifier reaching the page. facts.json `source` strings carry
    annotations like "MahaRERA project registration data
    (projectLegalLandAddressDetails.boundariesSouth)", and CLAUDE.md Section B
    forbids a JSON key, field name or dotted path in EITHER document;
  * a value's own inline "(per ...)" attribution surviving next to the
    numbered citation that already says the same thing.

The first also explains a misleading gate message: the dotted identifier
pattern-matches a hostname, so verify_charter_report_quality reported a
"bare-domain mention was never converted to a citation" -- right that something
was wrong, wrong about what.

Run directly: python test_charter_report_source_labels.py
"""

import charter_report as cr


# --- identifier annotations --------------------------------------------------

def test_a_dotted_json_key_is_stripped():
    out = cr._inline_citation_label(
        "MahaRERA project registration data (projectLegalLandAddressDetails.boundariesSouth)"
    )
    assert "projectLegalLandAddressDetails" not in out, out
    assert "MahaRERA project registration data" in out, out
    print("test_a_dotted_json_key_is_stripped: PASS")


def test_an_undotted_identifier_is_stripped_too():
    """This one never tripped the gate -- the bare-domain pattern needs a dot --
    so fixing only what the gate caught would have left it shipping silently."""
    out = cr._inline_citation_label("MahaRERA project registration data (projectLegalLandAddressDetails)")
    assert "projectLegalLandAddressDetails" not in out, out
    print("test_an_undotted_identifier_is_stripped_too: PASS")


def test_human_annotations_survive():
    """"Performa A-1" and "First Schedule" are written for a reader and are the
    whole reason the annotation is preserved at all."""
    assert "Performa A-1" in cr._inline_citation_label("Pranami Bliss IOD plan comp.pdf (Performa A-1)")
    assert "First Schedule" in cr._inline_citation_label("Title Report - RERA.pdf (First Schedule)")
    print("test_human_annotations_survive: PASS")


def test_a_mixed_source_keeps_the_human_half_and_drops_the_code_half():
    out = cr._inline_citation_label(
        "Pranami Bliss IOD plan comp.pdf (Performa A-1); "
        "MahaRERA project registration data (projectLegalLandAddressDetails.boundariesSouth)"
    )
    assert "Performa A-1" in out and "projectLegalLandAddressDetails" not in out, out
    assert not out.rstrip().endswith((";", ",")), f"separator left dangling: {out!r}"
    print("test_a_mixed_source_keeps_the_human_half_and_drops_the_code_half: PASS")


def test_urls_and_plain_sources_are_untouched():
    assert cr._inline_citation_label("https://zaubacorp.com/company/x") == "per zaubacorp.com"
    assert cr._polish_source("https://zaubacorp.com/company/x", "external") == "https://zaubacorp.com/company/x"
    print("test_urls_and_plain_sources_are_untouched: PASS")


def test_the_stripped_label_no_longer_trips_the_bare_domain_check():
    out = cr._inline_citation_label(
        "MahaRERA project registration data (projectLegalLandAddressDetails.boundariesSouth)"
    )
    assert not cr._BARE_DOMAIN_MENTION_RE.search(out), out
    print("test_the_stripped_label_no_longer_trips_the_bare_domain_check: PASS")


def test_both_label_paths_are_covered():
    """_inline_citation_label (Internal) and _polish_source (External/References)
    both render source strings, so both have to strip."""
    src = "MahaRERA data (someJsonKey.path)"
    assert "someJsonKey" not in cr._inline_citation_label(src)
    assert "someJsonKey" not in cr._polish_source(src, "external")
    assert "someJsonKey" not in cr._polish_source(src, "internal")
    print("test_both_label_paths_are_covered: PASS")


# --- redundant inline attribution --------------------------------------------

def test_inline_attribution_is_dropped_when_a_citation_will_render():
    group = {"total_gross_area": {
        "value": "1,674.63 sq. m. total plot (per MHADA offer letter and site survey), made up of two properties.",
        "source": "Pranami Bliss IOD plan comp.pdf",
    }}
    (_, (value, _)), = cr._cited_field_rows(group)
    assert "(per MHADA offer letter" not in value, value
    assert "1,674.63 sq. m. total plot" in value, value
    print("test_inline_attribution_is_dropped_when_a_citation_will_render: PASS")


def test_inline_attribution_is_kept_when_it_is_the_only_attribution():
    """With no source there is no citation to make it redundant, and stripping
    it would leave the value attributed to nothing."""
    group = {"total_gross_area": {
        "value": "1,674.63 sq. m. total plot (per MHADA offer letter and site survey).",
        "source": "",
    }}
    (_, (value, _)), = cr._cited_field_rows(group)
    assert "(per MHADA offer letter" in value, value
    print("test_inline_attribution_is_kept_when_it_is_the_only_attribution: PASS")


if __name__ == "__main__":
    test_a_dotted_json_key_is_stripped()
    test_an_undotted_identifier_is_stripped_too()
    test_human_annotations_survive()
    test_a_mixed_source_keeps_the_human_half_and_drops_the_code_half()
    test_urls_and_plain_sources_are_untouched()
    test_the_stripped_label_no_longer_trips_the_bare_domain_check()
    test_both_label_paths_are_covered()
    test_inline_attribution_is_dropped_when_a_citation_will_render()
    test_inline_attribution_is_kept_when_it_is_the_only_attribution()
    print("\nAll tests passed.")

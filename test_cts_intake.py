"""
Tests for run_cts_lookup_standalone -- the standalone CTS-only land-record
lookup (Phase 2 of the "identifier can arrive before a RERA number" work).
Mocks mahabhumi.search_cts_candidates/fetch_property_card (network + a real
browser/CAPTCHA) to keep this offline and fast; see the manual live check
noted in the PR/commit for a real-network proof (this one needs a human
to solve a CAPTCHA, so it can't be automated the way promoter_intake's
live check was).

The property that matters most, same reasoning as test_promoter_intake.py:
the "cts_land_record_check" key this writes must be IDENTICAL to the key
run_cts_land_lookup itself uses in facts.json for the same check -- pinned
down directly rather than assumed.

Run directly: python test_cts_intake.py
"""

import contextlib
import json
import os
import shutil

import mahabhumi
import company_charter as cc

_SCRATCH_DIR = os.path.join("output", "_test_scratch_cts_intake")


@contextlib.contextmanager
def _patch_mahabhumi(**funcs):
    originals = {name: getattr(mahabhumi, name) for name in funcs}
    for name, fn in funcs.items():
        setattr(mahabhumi, name, fn)
    try:
        yield
    finally:
        for name, fn in originals.items():
            setattr(mahabhumi, name, fn)


def test_output_key_matches_run_cts_land_lookups_own_facts_key():
    found_card = {"found": True, "fields": {"Owner": "Test Owner"}, "raw_text": "...", "url": "https://bhulekh.mahabhumi.gov.in/x"}
    with _patch_mahabhumi(
        search_cts_candidates=lambda district, office, village, cts: {"found": True, "candidates": ["100", "100/1"]},
        fetch_property_card=lambda district, office, village, cts, mobile, screenshot_path=None: found_card,
    ):
        record = cc.run_cts_lookup_standalone("Pune", "Pune City", "Wagholi", "100", "9999999999", output_dir=_SCRATCH_DIR)

    # This key must match facts["cts_land_record_check"] exactly -- that's
    # what lets a later run_cts_land_lookup pass for the same plot absorb
    # this file directly.
    assert record["cts_land_record_check"] == found_card
    print("test_output_key_matches_run_cts_land_lookups_own_facts_key: PASS")


def test_source_appended_only_when_found():
    found_card = {"found": True, "fields": {}, "raw_text": "", "url": "https://bhulekh.mahabhumi.gov.in/x"}
    with _patch_mahabhumi(
        search_cts_candidates=lambda district, office, village, cts: {"found": True, "candidates": ["100"]},
        fetch_property_card=lambda district, office, village, cts, mobile, screenshot_path=None: found_card,
    ):
        record = cc.run_cts_lookup_standalone("Pune", "Pune City", "Wagholi", "100", "9999999999", output_dir=_SCRATCH_DIR)

    assert len(record["sources"]) == 1
    assert record["sources"][0]["topic"] == "land_record"
    assert "100" in record["sources"][0]["ref"] and "Wagholi" in record["sources"][0]["ref"]
    print("test_source_appended_only_when_found: PASS")


def test_cts_not_in_candidates_is_a_clean_not_found_not_a_crash():
    with _patch_mahabhumi(
        search_cts_candidates=lambda district, office, village, cts: {"found": True, "candidates": ["200", "201"]},
        fetch_property_card=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch when CTS isn't an exact candidate match")),
    ):
        record = cc.run_cts_lookup_standalone("Pune", "Pune City", "Wagholi", "100", "9999999999", output_dir=_SCRATCH_DIR)

    assert record["cts_land_record_check"]["found"] is False
    assert "not an exact match" in record["cts_land_record_check"]["note"]
    assert record["sources"] == []
    print("test_cts_not_in_candidates_is_a_clean_not_found_not_a_crash: PASS")


def test_captcha_timeout_is_caught_not_raised():
    with _patch_mahabhumi(
        search_cts_candidates=lambda district, office, village, cts: {"found": True, "candidates": ["100"]},
        fetch_property_card=lambda *a, **k: (_ for _ in ()).throw(mahabhumi.CaptchaTimeoutError("timed out waiting for CAPTCHA")),
    ):
        record = cc.run_cts_lookup_standalone("Pune", "Pune City", "Wagholi", "100", "9999999999", output_dir=_SCRATCH_DIR)

    assert record["cts_land_record_check"]["found"] is False
    assert "did not complete" in record["cts_land_record_check"]["note"]
    print("test_captcha_timeout_is_caught_not_raised: PASS")


def test_writes_to_slugified_pending_directory():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    found_card = {"found": True, "fields": {}, "raw_text": "", "url": "https://bhulekh.mahabhumi.gov.in/x"}
    with _patch_mahabhumi(
        search_cts_candidates=lambda district, office, village, cts: {"found": True, "candidates": ["100/1"]},
        fetch_property_card=lambda district, office, village, cts, mobile, screenshot_path=None: found_card,
    ):
        record = cc.run_cts_lookup_standalone("Pune", "Pune City", "Wagholi Gaon", "100/1", "9999999999", output_dir=_SCRATCH_DIR)

    slug = cc._slugify_for_pending_key("Pune", "Wagholi Gaon", "100/1")
    written_path = os.path.join(_SCRATCH_DIR, "_pending", slug, "land_record.json")
    assert os.path.exists(written_path), f"expected a file at {written_path}"
    with open(written_path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk == record
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_writes_to_slugified_pending_directory: PASS")


def test_slugify_handles_slashes_and_whitespace():
    slug = cc._slugify_for_pending_key("Pune", "Wagholi Gaon", "100/1")
    assert "/" not in slug and " " not in slug
    assert slug == "Pune_Wagholi_Gaon_100_1"
    print("test_slugify_handles_slashes_and_whitespace: PASS")


def test_screenshot_path_is_computed_before_the_captcha_gated_fetch():
    """Confirmed live (real CAPTCHA solve against Pranami Bliss's own CTS
    183): the Property Card renders as a document/image, not structured
    HTML -- mahabhumi.fetch_property_card's OCR support is what actually
    recovers it, which only works if run_cts_lookup_standalone computes and
    passes a real screenshot_path into that call. Captures what was
    actually passed rather than trusting it was."""
    captured = {}

    def _fake_fetch(district, office, village, cts, mobile, screenshot_path=None):
        captured["screenshot_path"] = screenshot_path
        return {"found": True, "fields": {}, "raw_text": "", "ocr_text": "", "url": "https://bhulekh.mahabhumi.gov.in/x"}

    with _patch_mahabhumi(
        search_cts_candidates=lambda district, office, village, cts: {"found": True, "candidates": ["100"]},
        fetch_property_card=_fake_fetch,
    ):
        cc.run_cts_lookup_standalone("Pune", "Pune City", "Wagholi", "100", "9999999999", output_dir=_SCRATCH_DIR)

    slug = cc._slugify_for_pending_key("Pune", "Wagholi", "100")
    expected_path = os.path.join(_SCRATCH_DIR, "_pending", slug, "property_card_screenshot.png")
    assert captured["screenshot_path"] == expected_path
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_screenshot_path_is_computed_before_the_captcha_gated_fetch: PASS")


def test_scrape_result_page_ocr_recovers_real_text_from_an_image():
    """Not mocked -- a real PIL-drawn image with legible text, fed through
    the actual Tesseract OCR path _scrape_result_page uses, via a minimal
    fake Playwright `page` (only .content()/.screenshot()/.url are used by
    that function). Proves the OCR wiring itself works on this machine, not
    just that the code compiles."""
    from PIL import Image, ImageDraw

    scratch = os.path.join(_SCRATCH_DIR, "ocr_test")
    os.makedirs(scratch, exist_ok=True)
    screenshot_path = os.path.join(scratch, "shot.png")

    class _FakePage:
        url = "https://bhulekh.mahabhumi.gov.in/result"

        def content(self):
            return "<html><body></body></html>"  # no structured fields -- the whole point

        def screenshot(self, path, full_page=True):
            img = Image.new("RGB", (400, 100), color="white")
            draw = ImageDraw.Draw(img)
            draw.text((10, 30), "PROPERTY CARD TEST", fill="black")
            img.save(path)

    fake_page = _FakePage()
    fake_page.frames = [fake_page]  # page.frames always includes the main frame itself
    record = mahabhumi._scrape_result_page(fake_page, screenshot_path)
    assert os.path.exists(screenshot_path)
    assert "PROPERTY" in record["ocr_text"].upper()
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_scrape_result_page_ocr_recovers_real_text_from_an_image: PASS")


if __name__ == "__main__":
    test_output_key_matches_run_cts_land_lookups_own_facts_key()
    test_source_appended_only_when_found()
    test_cts_not_in_candidates_is_a_clean_not_found_not_a_crash()
    test_captcha_timeout_is_caught_not_raised()
    test_writes_to_slugified_pending_directory()
    test_slugify_handles_slashes_and_whitespace()
    test_screenshot_path_is_computed_before_the_captcha_gated_fetch()
    test_scrape_result_page_ocr_recovers_real_text_from_an_image()
    print("\nAll tests passed.")

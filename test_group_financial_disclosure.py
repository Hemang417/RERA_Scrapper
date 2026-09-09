"""
Guards on the group financial disclosure sweep -- balance sheet/P&L/
income-tax return documents found on OTHER group entities' Gujarat/
Jharkhand/Haryana/West Bengal/Uttar Pradesh/Tamil Nadu projects, via the
group-wide RERA sweep.

THE DISCIPLINE THIS FILE PINS: a project this pass never OPENED (the
group-wide RERA sweep did not run, or opened it but hit its detail limit)
must not be read the same as a project that WAS opened and carried no
matching document -- "never checked" and "checked, found none" are
different claims, and confusing them is the exact failure
test_computed_facts_reach_the_page.py exists to catch elsewhere in this
codebase.

Everything here is offline: `downloaders` and `ocr` are the seams. No
network call and no real OCR pass ever runs from this file.

Run directly: python test_group_financial_disclosure.py
"""

import os
import shutil

import group_financial_disclosure as gfd

_SCRATCH = os.path.join("output", "_test_scratch_group_financial_disclosure")

_BALANCE_SHEET = {"label": "Audited Balance Sheet 2023-24.pdf", "url": "https://example/doc1",
                   "uid": "uid-1"}
_SALE_AGREEMENT = {"label": "Sale Agreement.pdf", "url": "https://example/doc2", "uid": "uid-2"}


def _opened_project(state="JH", reg_no="JHARERA/PROJECT/35/2023", documents=None,
                    matched_entity="Pranami Builders Pvt Ltd"):
    return {
        "state": state, "reg_no": reg_no, "project_id": "35",
        "matched_entity": matched_entity,
        "detail_status": "opened",
        "detail": {"documents": documents if documents is not None else []},
    }


def test_a_misspelled_balance_sheet_label_still_matches():
    """WBRERA's own filed labels drop the first 'a' -- 'blance sheet',
    confirmed live -- and a label naming that specific typo must still be
    treated as a financial disclosure, not silently missed."""
    assert gfd._is_high_priority("Blance Sheet 2022-23.pdf")
    assert gfd._is_high_priority("Audited Balance Sheet.pdf")
    assert not gfd._is_high_priority("Balance of Payments Report.pdf"), (
        "must not match unrelated 'balance' text with no 'sheet' beside it"
    )
    print("test_a_misspelled_balance_sheet_label_still_matches: PASS")


def _unopened_project(state="TG", reg_no="TG-999", matched_entity="Some Entity"):
    return {
        "state": state, "reg_no": reg_no, "project_id": None,
        "matched_entity": matched_entity,
        "detail_status": "not opened (limit reached)",
    }


def test_no_rera_sweep_produces_a_named_limitation_not_a_fabricated_zero():
    """--group-sweep off (or the flag simply never passed a result through)
    must read as 'not run', never as 'ran and found nothing'."""
    result = gfd.sweep(None)
    assert result["checked"] == 0 and result["total"] == 0
    assert any("was not enabled" in note for note in result["limitations"]), result["limitations"]
    print("test_no_rera_sweep_produces_a_named_limitation_not_a_fabricated_zero: PASS")


def test_a_rera_sweep_that_found_zero_projects_is_named_differently_from_never_run():
    result = gfd.sweep({"projects": []})
    assert result["checked"] == 0 and result["total"] == 0
    assert any("found no projects" in note for note in result["limitations"]), result["limitations"]
    assert not any("was not enabled" in note for note in result["limitations"])
    print("test_a_rera_sweep_that_found_zero_projects_is_named_differently_from_never_run: PASS")


def test_a_matching_document_is_downloaded_ocrd_and_reaches_statements():
    rera_sweep = {"projects": [_opened_project(documents=[_BALANCE_SHEET, _SALE_AGREEMENT])]}
    calls = []

    def fake_downloader(entry, dest_path):
        calls.append(entry["label"])
        return {"status": "downloaded", "saved_path": dest_path}

    result = gfd.sweep(
        rera_sweep, downloaders={"JH": fake_downloader},
        ocr=lambda path: "Total assets Rs 12,00,00,000.", cache_dir=None,
    )
    assert calls == ["Audited Balance Sheet 2023-24.pdf"], \
        "the non-matching Sale Agreement should never have been downloaded"
    assert result["checked"] == 1 and result["total"] == 1
    assert len(result["statements"]) == 1
    statement = result["statements"][0]
    assert statement["label"] == "Audited Balance Sheet 2023-24.pdf"
    assert "12,00,00,000" in statement["text_excerpt"]
    assert statement["entities"] == ["Pranami Builders Pvt Ltd"]
    assert result["projects_checked"][0]["matches_found"] == 1
    print("test_a_matching_document_is_downloaded_ocrd_and_reaches_statements: PASS")


def test_a_second_call_serves_the_cached_text_without_calling_the_downloader_again():
    os.makedirs(_SCRATCH, exist_ok=True)
    rera_sweep = {"projects": [_opened_project(documents=[_BALANCE_SHEET])]}
    calls = []

    def fake_downloader(entry, dest_path):
        calls.append(entry["label"])
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write("irrelevant -- ocr is faked below")
        return {"status": "downloaded", "saved_path": dest_path}

    kwargs = dict(downloaders={"JH": fake_downloader}, ocr=lambda path: "Net profit Rs 5,00,000.",
                 cache_dir=_SCRATCH)
    first = gfd.sweep(rera_sweep, **kwargs)
    second = gfd.sweep(rera_sweep, **kwargs)

    assert len(calls) == 1, "the second run should have used the cache, not re-downloaded"
    assert first["statements"][0]["text_excerpt"] == second["statements"][0]["text_excerpt"]
    assert second["statements"][0]["cache_status"] == "cached"
    print("test_a_second_call_serves_the_cached_text_without_calling_the_downloader_again: PASS")


def test_a_project_with_no_matching_labels_is_checked_but_produces_no_statement():
    rera_sweep = {"projects": [_opened_project(documents=[_SALE_AGREEMENT])]}
    result = gfd.sweep(rera_sweep, downloaders={}, cache_dir=None)
    assert result["checked"] == 1 and result["total"] == 1
    assert result["statements"] == []
    assert result["projects_checked"][0]["documents_seen"] == 1
    assert result["projects_checked"][0]["matches_found"] == 0
    print("test_a_project_with_no_matching_labels_is_checked_but_produces_no_statement: PASS")


def test_a_project_whose_detail_carries_no_documents_key_is_silently_skipped():
    """MahaRERA, K-RERA and every other state whose fetch_project_summary
    never exposes a `documents` list -- nothing here checks the state by
    name, the absence of the key is what does the work."""
    rera_sweep = {"projects": [_opened_project(state="MH", reg_no="P51800077150", documents=[])]}
    result = gfd.sweep(rera_sweep, downloaders={}, cache_dir=None)
    assert result["checked"] == 1
    assert result["statements"] == []
    assert result["projects_checked"][0]["documents_seen"] == 0
    print("test_a_project_whose_detail_carries_no_documents_key_is_silently_skipped: PASS")


def test_a_project_never_opened_counts_toward_total_but_not_checked():
    rera_sweep = {"projects": [
        _opened_project(documents=[_BALANCE_SHEET]),
        _unopened_project(),
    ]}
    result = gfd.sweep(rera_sweep, downloaders={"JH": lambda entry, dest: {"status": "downloaded", "saved_path": dest}},
                       ocr=lambda path: "text", cache_dir=None)
    assert result["total"] == 2, "the unopened project must still count toward the denominator"
    assert result["checked"] == 1, "the unopened project must not be counted as checked"
    print("test_a_project_never_opened_counts_toward_total_but_not_checked: PASS")


def test_a_downloader_that_raises_is_named_never_dropped_silently():
    rera_sweep = {"projects": [_opened_project(documents=[_BALANCE_SHEET])]}

    def broken_downloader(entry, dest_path):
        raise ConnectionError("portal unreachable")

    result = gfd.sweep(rera_sweep, downloaders={"JH": broken_downloader}, cache_dir=None)
    assert result["statements"] == []
    assert any("ConnectionError" in note for note in result["limitations"]), result["limitations"]
    print("test_a_downloader_that_raises_is_named_never_dropped_silently: PASS")


def test_a_downloader_missing_for_a_state_with_matches_is_named_not_silently_zero():
    """downloaders={} (the offline seam) means 'no downloader available for
    any state' -- a real match with nowhere to download it must still be
    named in limitations, not just quietly absent from statements."""
    rera_sweep = {"projects": [_opened_project(documents=[_BALANCE_SHEET])]}
    result = gfd.sweep(rera_sweep, downloaders={}, cache_dir=None)
    assert result["statements"] == []
    assert any("no way to download" in note for note in result["limitations"]), result["limitations"]
    print("test_a_downloader_missing_for_a_state_with_matches_is_named_not_silently_zero: PASS")


def test_coverage_sentence_never_says_clean_and_leads_with_the_denominator():
    empty = gfd.coverage_sentence({})
    assert "No group-wide RERA sweep" in empty
    populated = gfd.coverage_sentence({"checked": 1, "total": 2, "statements": [{}]})
    assert "1 of 2" in populated
    assert "clean" not in populated.lower()
    print("test_coverage_sentence_never_says_clean_and_leads_with_the_denominator: PASS")


def _cleanup():
    shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_no_rera_sweep_produces_a_named_limitation_not_a_fabricated_zero()
        test_a_rera_sweep_that_found_zero_projects_is_named_differently_from_never_run()
        test_a_misspelled_balance_sheet_label_still_matches()
        test_a_matching_document_is_downloaded_ocrd_and_reaches_statements()
        test_a_second_call_serves_the_cached_text_without_calling_the_downloader_again()
        test_a_project_with_no_matching_labels_is_checked_but_produces_no_statement()
        test_a_project_whose_detail_carries_no_documents_key_is_silently_skipped()
        test_a_project_never_opened_counts_toward_total_but_not_checked()
        test_a_downloader_that_raises_is_named_never_dropped_silently()
        test_a_downloader_missing_for_a_state_with_matches_is_named_not_silently_zero()
        test_coverage_sentence_never_says_clean_and_leads_with_the_denominator()
        print("\nAll tests passed.")
    finally:
        _cleanup()

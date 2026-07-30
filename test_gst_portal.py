"""
Tests for gst_portal.py's parsing/orchestration logic -- mocks Playwright
entirely (a fake `page` object) to keep this offline; the actual live
portal behavior past the CAPTCHA gate is NOT YET independently confirmed
(see gst_portal.py's own module docstring), so these tests only cover what
doesn't depend on that: GSTIN extraction from arbitrary page text, the
CAPTCHA-wait loop's signal detection, and error degradation.

Run directly: python test_gst_portal.py
"""

import os
import shutil

from PIL import Image

import gst_portal


class _FakePage:
    def __init__(self, texts, url="https://services.gst.gov.in/services/searchtp"):
        """`texts` is a list of strings returned by successive inner_text()
        calls -- the first is the "before" state, the rest simulate the
        page settling after a CAPTCHA solve."""
        self._texts = list(texts)
        self.url = url
        self._closed = False

    def inner_text(self, selector):
        if len(self._texts) > 1:
            return self._texts.pop(0)
        return self._texts[0]

    def is_closed(self):
        return self._closed

    def screenshot(self, path, full_page=True):
        Image.new("RGB", (2, 2), color="white").save(path)  # a real, openable PNG


def test_wait_for_captcha_solve_detects_success_text():
    page = _FakePage(["Search Taxpayer", "Search Taxpayer", "Legal Name: TEST COMPANY"])
    gst_portal._wait_for_captcha_solve(page, timeout_seconds=5, poll_interval=0.01, success_texts=("Legal Name",))
    print("test_wait_for_captcha_solve_detects_success_text: PASS")


def test_wait_for_captcha_solve_detects_url_change():
    class _UrlChangingPage(_FakePage):
        def __init__(self):
            super().__init__(["same", "same", "same"])
            self._calls = 0

        @property
        def url(self):
            self._calls += 1
            return "https://services.gst.gov.in/services/searchtp" if self._calls < 3 else "https://services.gst.gov.in/services/searchtp/results"

        @url.setter
        def url(self, value):
            pass

    page = _UrlChangingPage()
    gst_portal._wait_for_captcha_solve(page, timeout_seconds=5, poll_interval=0.01, success_texts=("never appears",))
    print("test_wait_for_captcha_solve_detects_url_change: PASS")


def test_wait_for_captcha_solve_times_out_honestly():
    page = _FakePage(["nothing changes"] * 200)
    try:
        gst_portal._wait_for_captcha_solve(page, timeout_seconds=0.05, poll_interval=0.01, success_texts=("never appears",))
        assert False, "expected a CaptchaTimeoutError"
    except gst_portal.CaptchaTimeoutError as e:
        assert "wasn't solved in time" in str(e)
    print("test_wait_for_captcha_solve_times_out_honestly: PASS")


def test_wait_for_captcha_solve_raises_on_closed_browser():
    class _ClosedPage(_FakePage):
        def is_closed(self):
            return True

    page = _ClosedPage(["x"])
    try:
        gst_portal._wait_for_captcha_solve(page, timeout_seconds=5, poll_interval=0.01, success_texts=("x",))
        assert False, "expected a BrowserClosedError"
    except gst_portal.BrowserClosedError:
        pass
    print("test_wait_for_captcha_solve_raises_on_closed_browser: PASS")


def test_wait_for_captcha_solve_treats_transient_error_as_settling():
    class _FlakyPage(_FakePage):
        def __init__(self):
            super().__init__(["ok"])
            self._calls = 0

        def inner_text(self, selector):
            self._calls += 1
            if self._calls == 1:
                raise Exception("Execution context was destroyed, most likely because of a navigation")
            return "Legal Name: recovered"

    page = _FlakyPage()
    gst_portal._wait_for_captcha_solve(page, timeout_seconds=5, poll_interval=0.01, success_texts=("Legal Name",))
    print("test_wait_for_captcha_solve_treats_transient_error_as_settling: PASS")


def test_ocr_screenshot_never_raises_on_bad_path():
    result = gst_portal._ocr_screenshot(_FakePage(["x"]), os.path.join("output", "_does_not_exist", "shot.png"))
    assert result.startswith("[OCR unavailable")
    print("test_ocr_screenshot_never_raises_on_bad_path: PASS")


def test_ocr_screenshot_succeeds_with_a_working_page():
    scratch = os.path.join("output", "_test_scratch_gst_portal")
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch, exist_ok=True)
    path = os.path.join(scratch, "shot.png")
    result = gst_portal._ocr_screenshot(_FakePage(["x"]), path)
    assert os.path.exists(path)
    assert not result.startswith("[OCR unavailable")  # a 2-pixel blank PNG OCRs to "" cleanly, not an error
    shutil.rmtree(scratch, ignore_errors=True)
    print("test_ocr_screenshot_succeeds_with_a_working_page: PASS")


class _FakeStopper:
    def stop(self):
        pass


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSearchPage(_FakePage):
    """Adds the navigation/fill/click surface search_gstins_by_pan and
    fetch_gstin_filing_table actually call, on top of _FakePage's
    inner_text/is_closed/screenshot."""

    def __init__(self, texts, click_error=None):
        super().__init__(texts)
        self.filled = {}
        self.clicked = []
        self._click_error = click_error

    def goto(self, url, timeout=30000):
        pass

    def wait_for_selector(self, selector, timeout=15000):
        pass

    def fill(self, selector, value):
        self.filled[selector] = value

    def click(self, selector, timeout=10000):
        self.clicked.append(selector)
        if self._click_error:
            raise self._click_error

    def wait_for_timeout(self, ms):
        pass


def _patch_launch(monkeypatched_page):
    real_launch = gst_portal._launch
    gst_portal._launch = lambda headless: (_FakeStopper(), _FakeBrowser(), monkeypatched_page)
    return real_launch


def test_search_gstins_by_pan_extracts_gstins_from_results_text():
    page = _FakeSearchPage(["before", "before", "GSTIN: 27AANCM5273D1ZA State: Maharashtra Legal Name: TEST"])
    real_launch = _patch_launch(page)
    try:
        result = gst_portal.search_gstins_by_pan("AANCM5273D", timeout_seconds=5, poll_interval=0.01)
    finally:
        gst_portal._launch = real_launch

    assert result["found"] is True
    assert result["gstins"] == ["27AANCM5273D1ZA"]
    assert page.filled[gst_portal._SEL_INPUT] == "AANCM5273D"
    print("test_search_gstins_by_pan_extracts_gstins_from_results_text: PASS")


def test_search_gstins_by_pan_honest_not_found_on_timeout():
    page = _FakeSearchPage(["nothing changes"] * 50)
    real_launch = _patch_launch(page)
    try:
        result = gst_portal.search_gstins_by_pan("AANCM5273D", timeout_seconds=0.03, poll_interval=0.01)
    finally:
        gst_portal._launch = real_launch

    assert result["found"] is False
    assert result["gstins"] == []
    assert "wasn't solved in time" in result["note"]
    print("test_search_gstins_by_pan_honest_not_found_on_timeout: PASS")


def test_fetch_gstin_filing_table_clicks_filing_table_button():
    page = _FakeSearchPage(["before", "before", "Legal Name: TEST COMPANY"])
    real_launch = _patch_launch(page)
    try:
        result = gst_portal.fetch_gstin_filing_table("27AANCM5273D1ZA", timeout_seconds=5, poll_interval=0.01)
    finally:
        gst_portal._launch = real_launch

    assert result["found"] is True
    assert gst_portal._SEL_FILING_TABLE_BTN in page.clicked
    assert page.filled[gst_portal._SEL_INPUT] == "27AANCM5273D1ZA"
    print("test_fetch_gstin_filing_table_clicks_filing_table_button: PASS")


def test_fetch_gstin_filing_table_degrades_honestly_if_button_click_fails():
    """If #filingTable doesn't work as expected once a real run happens
    (unconfirmed layout -- see module docstring), registration details
    already found must still come back, not get thrown away."""
    page = _FakeSearchPage(["before", "before", "Legal Name: TEST COMPANY"], click_error=Exception("selector not found"))
    real_launch = _patch_launch(page)
    try:
        result = gst_portal.fetch_gstin_filing_table("27AANCM5273D1ZA", timeout_seconds=5, poll_interval=0.01)
    finally:
        gst_portal._launch = real_launch

    assert result["found"] is True
    assert "did not work as expected" in result["note"]
    print("test_fetch_gstin_filing_table_degrades_honestly_if_button_click_fails: PASS")


if __name__ == "__main__":
    test_wait_for_captcha_solve_detects_success_text()
    test_wait_for_captcha_solve_detects_url_change()
    test_wait_for_captcha_solve_times_out_honestly()
    test_wait_for_captcha_solve_raises_on_closed_browser()
    test_wait_for_captcha_solve_treats_transient_error_as_settling()
    test_ocr_screenshot_never_raises_on_bad_path()
    test_ocr_screenshot_succeeds_with_a_working_page()
    test_search_gstins_by_pan_extracts_gstins_from_results_text()
    test_search_gstins_by_pan_honest_not_found_on_timeout()
    test_fetch_gstin_filing_table_clicks_filing_table_button()
    test_fetch_gstin_filing_table_degrades_honestly_if_button_click_fails()
    print("\nAll tests passed.")

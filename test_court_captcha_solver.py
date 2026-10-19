"""Offline tests for the court CAPTCHA vision helpers and integrations."""

from unittest import mock

import captcha_solver
import nclt_search
import bombay_hc_search


class _FakeLocator:
    def __init__(self):
        self.wait_state = None

    def wait_for(self, state, timeout):
        self.wait_state = (state, timeout)

    def screenshot(self):
        return b"captcha-image"


class _FakePage:
    def __init__(self):
        self.locator_calls = []
        self.fake_locator = _FakeLocator()

    def locator(self, selector):
        self.locator_calls.append(selector)
        return mock.Mock(first=self.fake_locator)


class _FakeNcltPage:
    def __init__(self, bodies):
        self.bodies = iter(bodies)
        self.body = ""
        self.locator_calls = []

    def locator(self, selector):
        self.locator_calls.append(selector)
        return mock.Mock()

    def expect_request(self, predicate, timeout):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def click(self, selector, timeout=None):
        self.body = next(self.bodies)

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def inner_text(self, selector):
        assert selector == "body"
        return self.body

    def is_closed(self):
        return False

    def wait_for_selector(self, *_args, **_kwargs):
        return None

    def select_option(self, *_args, **_kwargs):
        return None

    def fill(self, *_args, **_kwargs):
        return None


class _FakeBhcResponse:
    def __init__(self, body):
        self.body = body

    def text(self):
        return self.body


class _FakeBhcResponseScope:
    def __init__(self, page):
        self.page = page
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.value = _FakeBhcResponse(next(self.page.responses))
        return False


class _FakeBhcLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def click(self, **_kwargs):
        if self.selector == bombay_hc_search._SEL_CAPTCHA_REFRESH:
            self.page.image_src += "-fresh"

    def fill(self, *_args, **_kwargs):
        return None

    def press_sequentially(self, *_args, **_kwargs):
        return None

    def get_attribute(self, name):
        assert name == "src"
        return self.page.image_src


class _FakeBhcPage:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.image_src = "captcha.png?first"
        self.url = bombay_hc_search._MAIN_URL
        self.go_clicks = 0

    def locator(self, selector):
        return _FakeBhcLocator(self, selector)

    def fill(self, *_args, **_kwargs):
        return None

    def check(self, *_args, **_kwargs):
        return None

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def expect_response(self, *_args, **_kwargs):
        return _FakeBhcResponseScope(self)

    def click(self, selector, **_kwargs):
        if selector == bombay_hc_search._SEL_GO_BUTTON:
            self.go_clicks += 1

    def wait_for_function(self, *_args, **_kwargs):
        return None

    def is_closed(self):
        return False


class _FakeBrowser:
    def close(self):
        return None


class _FakePlaywright:
    def stop(self):
        return None


def test_direct_court_solver_helpers_require_explicit_enable():
    page = _FakePage()
    with mock.patch.dict("os.environ", {
        "NCLT_CAPTCHA_AUTOSOLVE": "0",
        "BOMBAY_HC_CAPTCHA_AUTOSOLVE": "0",
    }), mock.patch.object(captcha_solver, "_solve_with_claude_vision") as solve:
        for solver in (
            captcha_solver.solve_nclt_captcha,
            captcha_solver.solve_bombay_hc_captcha,
        ):
            try:
                solver(page)
            except captcha_solver.CaptchaSolveError as exc:
                assert "not enabled" in str(exc)
            else:
                raise AssertionError(f"{solver.__name__} unexpectedly ran without opt-in")
        solve.assert_not_called()
    print("test_direct_court_solver_helpers_require_explicit_enable: PASS")


def test_court_search_autosolve_defaults_on_and_can_be_disabled():
    with mock.patch.dict("os.environ", {}, clear=True):
        assert nclt_search._autosolve_enabled() is True
        assert bombay_hc_search._autosolve_enabled() is True

    with mock.patch.dict("os.environ", {
        "NCLT_CAPTCHA_AUTOSOLVE": "0",
        "BOMBAY_HC_CAPTCHA_AUTOSOLVE": "0",
    }, clear=True):
        assert nclt_search._autosolve_enabled() is False
        assert bombay_hc_search._autosolve_enabled() is False

    print("test_court_search_autosolve_defaults_on_and_can_be_disabled: PASS")


def test_nclt_solver_uses_claude_vision():
    page = _FakePage()
    with mock.patch.dict("os.environ", {"NCLT_CAPTCHA_AUTOSOLVE": "0"}), \
         mock.patch.object(captcha_solver, "_anthropic_api_key", return_value="test-key"), \
         mock.patch.object(captcha_solver, "_solve_with_claude_vision", return_value="Ab12Cd") as solve:
        backend, answer = captcha_solver.solve_nclt_captcha(page, enabled=True)

    assert backend == "Claude vision"
    assert answer == "Ab12Cd"
    assert page.locator_calls == ["img.imgcaptcha"]
    assert page.fake_locator.wait_state == ("visible", 15000)
    assert solve.call_args.args[0] == b"captcha-image"
    assert solve.call_args.args[2] == "nclt"
    print("test_nclt_solver_uses_claude_vision: PASS")


def test_bombay_hc_solver_uses_claude_vision():
    page = _FakePage()
    with mock.patch.dict("os.environ", {"BOMBAY_HC_CAPTCHA_AUTOSOLVE": "1"}), \
         mock.patch.object(captcha_solver, "_anthropic_api_key", return_value="test-key"), \
         mock.patch.object(captcha_solver, "_solve_with_claude_vision", return_value="aB12cD") as solve:
        backend, answer = captcha_solver.solve_bombay_hc_captcha(page)

    assert backend == "Claude vision"
    assert answer == "aB12cD"
    assert page.locator_calls == ["#captcha_image"]
    assert page.fake_locator.wait_state == ("visible", 15000)
    assert solve.call_args.args[0] == b"captcha-image"
    assert solve.call_args.args[2] == "bombay_hc"
    print("test_bombay_hc_solver_uses_claude_vision: PASS")


def test_nclt_autosolve_retries_only_after_explicit_rejection():
    page = _FakeNcltPage([
        "The CAPTCHA entered is incorrect. Please try again with a new image.",
        "No Record Found In Case Detail.",
    ])
    with mock.patch("captcha_solver.solve_nclt_captcha", side_effect=[
        ("Claude vision", "Wrong1"),
        ("Claude vision", "Right2"),
    ]) as solve:
        nclt_search._autosolve_and_submit(page, "Some Promoter LLP", "Mumbai", 30)

    assert solve.call_count == 2
    assert page.locator_calls == ['input[name="answer"]', 'input[name="answer"]']
    print("test_nclt_autosolve_retries_only_after_explicit_rejection: PASS")


def test_nclt_autosolve_does_not_retry_unknown_response():
    page = _FakeNcltPage(["A page with no explicit CAPTCHA rejection indicator, so stop here."])
    with mock.patch("captcha_solver.solve_nclt_captcha", return_value=("Claude vision", "Maybe1")) as solve:
        nclt_search._autosolve_and_submit(page, "Some Promoter LLP", "Mumbai", 30)

    solve.assert_called_once()
    print("test_nclt_autosolve_does_not_retry_unknown_response: PASS")


def test_bombay_hc_autosolve_retries_on_confirmed_rejection():
    page = _FakeBhcPage(['{"Error":"ERROR_VAL"}', '{"ok":true}'])
    with mock.patch.dict("os.environ", {}, clear=True), \
         mock.patch.object(bombay_hc_search, "_launch", return_value=(_FakePlaywright(), _FakeBrowser(), page)), \
         mock.patch.object(bombay_hc_search, "_open_party_name_form"), \
         mock.patch("captcha_solver.solve_bombay_hc_captcha", return_value=("Claude vision", "Ab12Cd")) as solve:
        result = bombay_hc_search.search_party("Some Promoter LLP", 2026)

    assert result["raw_response"] == {"ok": True}
    assert page.go_clicks == 2
    assert page.image_src.endswith("-fresh")
    assert solve.call_count == 2
    print("test_bombay_hc_autosolve_retries_on_confirmed_rejection: PASS")


if __name__ == "__main__":
    test_direct_court_solver_helpers_require_explicit_enable()
    test_court_search_autosolve_defaults_on_and_can_be_disabled()
    test_nclt_solver_uses_claude_vision()
    test_bombay_hc_solver_uses_claude_vision()
    test_nclt_autosolve_retries_only_after_explicit_rejection()
    test_nclt_autosolve_does_not_retry_unknown_response()
    test_bombay_hc_autosolve_retries_on_confirmed_rejection()
    print("\nAll tests passed.")

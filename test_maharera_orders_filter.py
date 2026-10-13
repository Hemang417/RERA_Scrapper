"""Regression test for the MahaRERA orders/judgements respondent-name
filter false-positive bug found live 2026-10-13 (see
company_charter._maharera_orders_search_once's own module note, and the
memory file project_maharera_domain_outage.md from that engagement).

Confirmed live: searching 57 different real promoter names in one pass
returned the SAME ~113 orders (1640 rows total) for every one of them, none
of which actually named the promoter searched for -- the portal's
"ask once more via the session" fallback GET had landed on its own generic,
unfiltered recent-orders listing rather than a page reflecting the filter,
and the code had no way to tell the two apart by marker alone.

This test exercises _maharera_orders_search_once directly against crafted
HTML (no live network call), covering the three cases that must never be
confused: a genuine filtered hit, the unfiltered-listing leak (must now be
treated as "filter never applied", i.e. None), and a genuine "No Record"
nil.
"""
import company_charter as cc

_FORM_HTML = """
<form id="orders-judgements-form">
  <input type="text" name="order_project_name" value="">
  <input type="text" name="order_respondent_name" value="">
  <select name="order_complaint_type">
    <option value="rulings_of_MahaRERA" selected>Rulings</option>
  </select>
  <input type="radio" name="orders_judgements_type" value="orders" checked>
  <input type="text" name="ruling_judgement_from" value="">
  <input type="text" name="ruling_judgement_to" value="">
</form>
"""


def _card(respondent_name, project_name="Some Project"):
    return f"""
    <div class="shadow rounded p-3">
      <h4>{project_name}</h4>
      <p class="p-0">#REG123</p>
      <label>Complainant Name</label><p>Some Complainant</p>
      <label>Complainant No</label><p>CC000000000000001</p>
      <label>Respondent Name</label><p>{respondent_name}</p>
      <label>Uploaded Date</label><p>01-01-2026</p>
    </div>
    """


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeCookies:
    def set(self, *a, **k):
        pass


class _FakeSession:
    """Models the real flow: first GET returns the form, the POST itself
    never carries the results region (forcing the fallback GET path this
    bug lives in), and the fallback GET returns whatever `listing_html` the
    test configures -- a genuinely filtered page, the unfiltered-listing
    leak, or a real "No Record" nil."""

    def __init__(self, listing_html):
        self.cookies = _FakeCookies()
        self._listing_html = listing_html
        self._get_calls = 0

    def get(self, url, headers=None, params=None, timeout=None):
        if params and params.get("page"):
            return _FakeResponse("<html></html>")  # no further pagination
        self._get_calls += 1
        # First call is the initial form fetch; the second is the fallback
        # listing fetch this bug concerns itself with.
        return _FakeResponse(_FORM_HTML if self._get_calls == 1 else self._listing_html)

    def post(self, url, data=None, headers=None, timeout=None):
        return _FakeResponse("<html><form id='orders-judgements-form'></form></html>")


def _run(monkeypatch, listing_html, respondent_name):
    session = _FakeSession(listing_html)
    monkeypatch.setattr(cc.requests, "Session", lambda: session)
    return cc._maharera_orders_search_once(respondent_name=respondent_name)


def test_unfiltered_listing_leak_is_rejected_not_returned_as_a_result(monkeypatch):
    """The exact bug: the fallback GET returns cards for unrelated
    promoters. None of them name the respondent that was searched for, so
    this must come back None (filter never applied), never as rows -- a
    caller treating None as "nothing found" would fabricate a false clean
    record, and treating the raw rows as found would fabricate false
    litigation hits, confirmed live for 57/57 names in one real run."""
    listing = "<html>" + _ORDERS_MARKER_HTML(
        _card("Mahindra Happinest Developers Ltd", "Galaxy Apartments"),
        _card("Era Realtors Pvt. Ltd.", "Alta Monte and Signet"),
    ) + "</html>"
    result = _run(monkeypatch, listing, respondent_name="Eklavya Builders Private Limited")
    assert result is None


def test_a_genuinely_matching_respondent_is_still_returned(monkeypatch):
    """A real hit -- at least one row's own respondent name actually
    relates to the query -- must still come through, so the guard above
    cannot be a blanket "respondent search always returns nothing"."""
    listing = "<html>" + _ORDERS_MARKER_HTML(
        _card("Eklavya Builders Private Limited", "Some Real Project"),
    ) + "</html>"
    result = _run(monkeypatch, listing, respondent_name="Eklavya Builders Private Limited")
    assert result is not None
    assert len(result) == 1
    assert result[0]["respondent_name"] == "Eklavya Builders Private Limited"


def test_genuine_no_record_nil_still_returns_empty_list(monkeypatch):
    """The portal's own honest "No Record" text must still read as a
    confirmed-clean empty list, not as None -- this case was already
    correct before the fix and must not regress."""
    listing = "<html>No Record Found</html>"
    result = _run(monkeypatch, listing, respondent_name="Eklavya Builders Private Limited")
    assert result == []


def _ORDERS_MARKER_HTML(*cards):
    # _ORDERS_RESULT_MARKER is "bg-body rounded" -- include it once so the
    # page reads as carrying the results region, exactly as the real
    # listing page does.
    return "<div class='bg-body rounded'></div>" + "".join(cards)

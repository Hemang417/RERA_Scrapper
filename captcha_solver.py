"""
Image-CAPTCHA autosolving, used by gst_portal.py (solve_gst_captcha),
session_auth.py (solve_maharera_captcha), and igr_maharashtra_search.py
(solve_igr_captcha), and optionally the direct NCLT / Bombay High Court
searches. Claude vision only (claude-sonnet-5, same model/SDK pattern as
deep_research.py) -- raises past a failed call so the caller can retry,
rather than falling back to a second OCR backend or a human-solve wait
loop. The court-portal CAPTCHA adapters have not been validated with a live
answer.

THIS IS A POLICY REVERSAL FROM EVERY OTHER CAPTCHA-GATED MODULE IN THIS
PIPELINE. Both gst_portal.py's and session_auth.py's own docstrings say
plainly that nothing there solves or reads the CAPTCHA image itself --
that policy was deliberate, not a stopgap, see guardrails.md's "HUMAN
budget" framing. Before wiring this into a live run against a government
portal, confirm this doesn't violate its Terms of Use. This module does
not decide that; it only provides the mechanism, gated behind an explicit
opt-in (see each solve_*_captcha function's `enabled` kwarg / its own
*_CAPTCHA_AUTOSOLVE env var) so it can never fire silently.

CapSolver was the original second backend (tried whenever Claude vision
wasn't configured or failed) and was REMOVED 2026-10-15: CONFIRMED LIVE it
could not reliably solve any of this codebase's portals once Claude vision
was actually working (IGR's case-sensitive mixed-case CAPTCHA in
particular -- CapSolver returned the same wrong short guess across every
retry attempt, exhausting the whole autosolve budget on a CAPTCHA Claude
vision could read correctly on its own). The human-solve wait loop each
caller used to fall back to next was ALSO removed from this pipeline's own
retry loops (session_auth.py/gst_portal.py/igr_maharashtra_search.py) for
the same reason this whole automation effort exists: a fully unattended
run has no one there to solve it, so waiting on one is pure dead time
rather than a real fallback. Claude vision is now the only backend; a
caller's own retry loop (see e.g. igr_maharashtra_search.py's
_MAX_AUTOSOLVE_ATTEMPTS) is what absorbs an occasional misread, backed by
the $5 cross-run budget cap below as the actual stopping condition if
retries alone were ever to run away.

Flow:
  1. Screenshot just the CAPTCHA element (raw, no preprocessing -- a
     vision-capable model reads full visual context better than a
     stripped-down binarized image, unlike a plain OCR engine).
  2. Ask Claude to read the text back, with a portal-specific format hint
     in the prompt (exact length/alphabet/case where confirmed).
  3. Caller types it into the CAPTCHA input and submits -- this module
     never touches the Search button itself.

CONFIRMED LIVE 2026-10-14 against services.gst.gov.in/services/searchtpbypan:
  - CAPTCHA image: id="imgCaptcha" (class="captcha") -- does NOT mount into
    the DOM until the PAN/GSTIN field validates, which needs an Enter
    keypress (or blur) after filling it; see gst_portal.py's page.press(...).
  - The <img> can report "visible" before its src has actually finished
    loading pixels -- screenshotting that early produces a valid-but-blank
    image. Waiting for the element's own naturalWidth confirms real pixel
    data.
"""

from __future__ import annotations

import base64
import io
import json
import os

from anthropic import Anthropic
from PIL import Image, ImageOps

_CLAUDE_VISION_MODEL = "claude-sonnet-5"
_claude_client = None

_ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_env_file_loaded = False


def _load_dotenv_once() -> None:
    """CONFIRMED LIVE 2026-10-15: a Machine-level ANTHROPIC_API_KEY set via
    Windows System Properties is NOT visible to this process even after a
    full reboot, because this code runs inside a sandboxed tool-execution
    environment that deliberately does not forward that specific variable
    (likely to avoid exposing the credential backing that environment's own
    LLM calls to arbitrary scripts it runs) -- CAPSOLVER_API_KEY/
    TWOCAPTCHA_API_KEY, unrelated names, pass through untouched. No amount
    of restarting/rebooting fixes this; it is not a staleness issue. A
    project-local .env file sidesteps it entirely by never depending on
    process environment inheritance at all -- read once, merged into
    os.environ only for keys not already set there (so a real environment
    variable always wins if one IS present)."""
    global _env_file_loaded
    if _env_file_loaded:
        return
    _env_file_loaded = True
    try:
        with open(_ENV_FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _anthropic_api_key() -> str:
    """ANTHROPIC_API_KEY, checking the real process environment first (so a
    genuine env var always wins) and falling back to the project-local
    .env file (see _load_dotenv_once) -- the only reliable path in this
    sandboxed execution environment, see that function's own docstring."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    _load_dotenv_once()
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()

# $ per 1M tokens -- same intro rate deep_research.py already uses for this
# model (see that module's own _PRICING_PER_1M_TOKENS comment for the
# source/expiry date). Image input is billed as ordinary input tokens by
# the API, already reflected in the real usage.input_tokens a response
# reports -- no separate per-image estimate needed.
_CLAUDE_PRICING_PER_1M_TOKENS = {"claude-sonnet-5": {"input": 2.00, "output": 10.00}}

# Mirrors the $5 cap set on this API key itself in the Anthropic Console --
# NOT a substitute for that account-level limit (this only stops further
# CALLS FROM THIS CODEBASE; it can't touch spend from anywhere else on the
# same key, and a crash between an API call and the usage-file write below
# could under-count by at most one call's cost). Persisted to disk, not
# just in-process, because each pipeline run is its own fresh `python`
# process -- an in-memory-only cap would silently reset every run.
_CAPTCHA_SOLVE_COST_CAP_USD = 5.0
_CAPTCHA_USAGE_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captcha_solve_usage.json")


def _load_captcha_usage() -> dict:
    try:
        with open(_CAPTCHA_USAGE_LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"total_cost_usd": 0.0, "total_calls": 0, "by_portal": {}}


def _save_captcha_usage(usage: dict) -> None:
    try:
        with open(_CAPTCHA_USAGE_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(usage, f, indent=2)
    except Exception:
        pass  # usage tracking must never break an otherwise-successful solve


def _captcha_budget_remaining_usd() -> float:
    return _CAPTCHA_SOLVE_COST_CAP_USD - _load_captcha_usage().get("total_cost_usd", 0.0)


def _record_claude_vision_usage(portal: str, model: str, resp) -> float:
    """Records this call's ACTUAL cost (from the API response's own usage
    object -- never estimated upfront) against the persisted, cross-run
    budget. Returns this one call's cost in USD."""
    usage = getattr(resp, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    price = _CLAUDE_PRICING_PER_1M_TOKENS.get(model)
    cost_usd = round((input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000, 6) if price else 0.0

    log = _load_captcha_usage()
    log["total_cost_usd"] = round(log.get("total_cost_usd", 0.0) + cost_usd, 6)
    log["total_calls"] = log.get("total_calls", 0) + 1
    portal_bucket = log.setdefault("by_portal", {}).setdefault(portal, {"calls": 0, "cost_usd": 0.0})
    portal_bucket["calls"] += 1
    portal_bucket["cost_usd"] = round(portal_bucket["cost_usd"] + cost_usd, 6)
    _save_captcha_usage(log)
    return cost_usd


def captcha_usage_summary() -> dict:
    """Public reporting helper -- answers "how many CAPTCHAs can we solve
    per dollar", the question this tracker was built for. Call any time,
    e.g. `python -c "import captcha_solver as c; print(c.captcha_usage_summary())"`."""
    log = _load_captcha_usage()
    total_cost = log.get("total_cost_usd", 0.0)
    total_calls = log.get("total_calls", 0)
    avg_cost_per_call = round(total_cost / total_calls, 6) if total_calls else 0.0
    return {
        "total_cost_usd": round(total_cost, 6),
        "total_calls": total_calls,
        "avg_cost_per_call_usd": avg_cost_per_call,
        "captchas_per_dollar": round(1 / avg_cost_per_call, 1) if avg_cost_per_call else None,
        "budget_cap_usd": _CAPTCHA_SOLVE_COST_CAP_USD,
        "budget_remaining_usd": round(_CAPTCHA_SOLVE_COST_CAP_USD - total_cost, 6),
        "by_portal": log.get("by_portal", {}),
    }


_SEL_GST_CAPTCHA_IMAGE = "#imgCaptcha"

# CONFIRMED against real captured CAPTCHA images 2026-10-15 (pixel-sampled
# offline, two separate instances): the diagonal strike-through line is
# reddish (R channel clearly dominant, G/B near 0), cleanly removable by
# color. The grid background's own brightness is NOT consistent across
# different CAPTCHA instances, though -- a single FIXED binarize threshold
# (tried first) correctly isolated the digits on one instance but wiped the
# entire image to blank white on another; Otsu's adaptive threshold (tried
# second) picked a split that merged digits and grid into an unreadable
# mess on that same instance. Both hard-binarization approaches are too
# brittle. What actually worked on every instance tested: skip binarizing
# entirely -- strip the red line by color, convert to grayscale, autocontrast
# (stretches whatever range that image actually uses), upscale. The digit
# strokes render bolder/darker than the grid cells either way, and leaving
# the output as grayscale rather than forcing a brittle black/white call
# can't fail catastrophically the way a binarize threshold can.
_RED_LINE_DOMINANCE = 30
_UPSCALE_FACTOR = 5

# CONFIRMED LIVE 2026-10-14 against maharerait.maharashtra.gov.in's project
# detail page: the CAPTCHA is a <canvas id="captcahCanvas"> (that misspelling
# is the SITE's own id, not a typo introduced here) -- plain rendered text on
# a flat background, no grid noise or strike-through line like GST's, so no
# preprocessing is applied for this one. Refresh is <a class="cpt-btn">.
_SEL_MAHARERA_CAPTCHA_CANVAS = "#captcahCanvas"
_SEL_MAHARERA_CAPTCHA_REFRESH_BTN = "a.cpt-btn"


class CaptchaSolveError(Exception):
    pass


class CaptchaSolverNotConfigured(CaptchaSolveError):
    pass


# CONFIRMED LIVE 2026-10-15: `naturalWidth > 0` is NOT sufficient to prove
# the real CAPTCHA has rendered -- captured offline across 5 fresh page
# loads (no submissions made), 2 of 5 (40%) produced a near-blank loading
# placeholder (a pale, low-contrast background, sometimes with helper text
# like "Enter valid letters shown in...") that still satisfied
# naturalWidth > 0, because the placeholder graphic itself has nonzero
# dimensions even though the real digits haven't rendered into it yet.
# This alone plausibly explains a chunk of earlier solve failures
# (including one where Claude correctly reported "I'm not able to see any
# text" -- it was right, the image genuinely was blank). Measured
# stddev/mean cleanly separate the two cases: blank placeholders had
# stdev 5.6-28.1 / mean 198.4-243.1 (flat, bright); real captchas had
# stdev 64.4-72.3 / mean 83.6-87.8 (high-contrast, dark). Thresholds below
# sit safely in the gap.
_BLANK_IMAGE_MAX_STDDEV = 40
_BLANK_IMAGE_MIN_MEAN = 150


def _looks_like_real_captcha(image_bytes: bytes) -> bool:
    try:
        from PIL import ImageStat
        gray = Image.open(io.BytesIO(image_bytes)).convert("L")
        stat = ImageStat.Stat(gray)
        return stat.stddev[0] > _BLANK_IMAGE_MAX_STDDEV or stat.mean[0] < _BLANK_IMAGE_MIN_MEAN
    except Exception:
        return True  # can't tell -- don't block on a measurement failure


def _screenshot_real_captcha(locator, page, timeout_ms: int = 10000) -> bytes:
    """Polls `locator.screenshot()` until it looks like a genuinely
    rendered CAPTCHA (see _looks_like_real_captcha), not just a loading
    placeholder that happens to satisfy naturalWidth > 0. Returns the last
    screenshot taken even if the timeout is reached without ever looking
    "real" -- the caller's own retry loop is the backstop for that rare
    case, not an exception here."""
    import time as _time
    deadline = _time.time() + (timeout_ms / 1000)
    last_bytes = locator.screenshot()
    while _time.time() < deadline:
        if _looks_like_real_captcha(last_bytes):
            return last_bytes
        page.wait_for_timeout(300)
        last_bytes = locator.screenshot()
    return last_bytes


def _preprocess_gst_captcha_image(image_bytes: bytes) -> bytes:
    """Strips the red strike-through line from a raw GST CAPTCHA
    screenshot and boosts contrast, WITHOUT hard-binarizing -- leaving
    bolder, darker digit strokes against a still-visible but lighter grid,
    rather than gambling on a single black/white cutoff.

    RESTORED 2026-10-15 after being removed along with CapSolver -- WRONGLY
    assumed at the time that "a vision model reads full visual context
    better than a stripped-down binarized image" the way it does for
    MahaRERA/IGR's clean, deliberately-human-readable CAPTCHAs. CONFIRMED
    LIVE this does not hold here: GST's CAPTCHA is genuinely adversarial
    (dense checkerboard background + a bold red strike-through line
    overlapping the digits, unlike anything else this codebase solves),
    and 6 consecutive live attempts on the RAW screenshot were ALL
    rejected, each a different wrong 6-digit guess -- not occasional
    misreads, totally wrong (e.g. Claude read '894712' on an image a human
    read as '560478'). A FIRST preprocessing attempt (red-line strip + a
    fixed-threshold hard binarize, like the version this codebase had
    before CapSolver's removal) fixed that one instance but then rendered a
    SECOND captured instance entirely blank white -- confirmed too brittle
    across different images' own brightness levels, not a real fix. This
    lighter version (no hard binarize at all) was the one that actually
    produced a correct-looking read on both tested instances. The lesson
    on top of the first one: "give the vision model raw context" is a
    reasonable default, NOT a universal rule, when a CAPTCHA's own noise is
    specifically engineered to obscure the signal -- but the FIX for that
    also needs to be robust across instances, not just the one sample that
    happened to be tested first.

    Never raises: on any processing failure, returns the original bytes
    unchanged so Claude still gets *something* rather than nothing."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        px = img.load()
        w, h = img.size
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y]
                if r - g > _RED_LINE_DOMINANCE and r - b > _RED_LINE_DOMINANCE:
                    px[x, y] = (255, 255, 255)
        gray = img.convert("L")
        gray = ImageOps.autocontrast(gray, cutoff=2)
        upscaled = gray.resize((w * _UPSCALE_FACTOR, h * _UPSCALE_FACTOR), Image.LANCZOS)
        out = io.BytesIO()
        upscaled.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return image_bytes


def _get_claude_client() -> Anthropic:
    global _claude_client
    if _claude_client is None:
        # CONFIRMED LIVE 2026-10-14: the anthropic SDK's own vendored HTTP
        # client (httpx2/httpcore2) depends on `truststore` for TLS, which
        # on this machine recurses infinitely (RecursionError) against
        # `pip-system-certs` -- both patch ssl.SSLContext.verify_mode, and
        # pip-system-certs patches it first (via a site-wide .pth hook that
        # runs before any of this code), so truststore's own "call the
        # original implementation" reference is already the patched one.
        # pip-system-certs stays installed (mahabhumi.py/session_auth.py's
        # own requests-based calls to maharerait.maharashtra.gov.in need
        # it -- see project memory "MahaRERA SSL fix"). The fix instead:
        # build httpx2's client with an explicit certifi cert bundle, which
        # makes it use a plain ssl.create_default_context() and never touch
        # truststore's conflicting path at all. Reproduced with a minimal
        # plain-text (non-vision) call too, so this is a pre-existing
        # environment conflict affecting any anthropic SDK use in this
        # codebase, not something specific to CAPTCHA solving.
        import certifi
        import httpx2
        _claude_client = Anthropic(api_key=_anthropic_api_key(), http_client=httpx2.Client(verify=certifi.where()))
    return _claude_client


def _solve_with_claude_vision(image_bytes: bytes, format_hint: str, portal: str) -> str:
    """Asks Claude to read the CAPTCHA image directly via vision, the same
    model/SDK call pattern deep_research.py already uses elsewhere in this
    codebase. `format_hint` carries the portal-specific constraint (exact
    length/alphabet/case where confirmed) -- it goes straight into the
    prompt rather than a structured API field, since a chat model has no
    separate "hint" channel. `portal` (e.g. "gst", "maharera") is just a
    usage-tracking label, see _record_claude_vision_usage.

    Raises CaptchaSolveError on any API failure (missing/invalid
    ANTHROPIC_API_KEY, rate limit, network error), if the response is empty
    (never silently returns a guess), or if the persisted cross-run budget
    (_CAPTCHA_SOLVE_COST_CAP_USD) is already exhausted -- checked BEFORE
    spending anything on this call, not after."""
    if not _anthropic_api_key():
        raise CaptchaSolveError("ANTHROPIC_API_KEY is not set -- required for Claude vision CAPTCHA solving.")
    remaining = _captcha_budget_remaining_usd()
    if remaining <= 0:
        raise CaptchaSolveError(
            f"Claude vision CAPTCHA-solving budget (${_CAPTCHA_SOLVE_COST_CAP_USD:.2f}) is exhausted "
            f"(${-remaining:.6f} over) -- see captcha_usage_summary()."
        )
    b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        resp = _get_claude_client().messages.create(
            model=_CLAUDE_VISION_MODEL,
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": (
                        f"Read the exact text shown in this CAPTCHA image. {format_hint} "
                        "Respond with ONLY the characters you see, nothing else -- no explanation, "
                        "no punctuation, no quotes, no leading/trailing whitespace."
                    )},
                ],
            }],
        )
    except Exception as e:
        raise CaptchaSolveError(f"Claude vision call failed: {e}") from e
    cost_usd = _record_claude_vision_usage(portal, _CLAUDE_VISION_MODEL, resp)
    text = "".join(
        getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    if not text:
        raise CaptchaSolveError(f"Claude vision returned no text (stop_reason={resp.stop_reason!r}, cost=${cost_usd:.6f}).")
    return text


_GST_FORMAT_HINT = "The text is always exactly 6 digits (0-9 only), never letters."


def solve_gst_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_GST_CAPTCHA_IMAGE,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA image on `page` (a Playwright Page, same
    object gst_portal.py already holds) and solves it via Claude vision on
    the PREPROCESSED screenshot (see _preprocess_gst_captcha_image --
    restored 2026-10-15 after 6 consecutive live failures on the raw image
    proved this portal's noise defeats Claude vision too, not just
    CapSolver). Returns (backend_name, solved_text). Raises
    CaptchaSolveError/CaptchaSolverNotConfigured if Claude vision isn't
    configured or the call fails -- the caller's own retry loop (see
    gst_portal.py's _solve_captcha_and_submit) is what absorbs an
    occasional misread.

    `enabled` must be passed True explicitly (or GST_CAPTCHA_AUTOSOLVE=1
    set in the environment) -- this is a deliberate double gate on top of
    requiring ANTHROPIC_API_KEY, so autosolve can never fire just because
    this module is imported or a key happens to be set in the environment
    for an unrelated reason."""
    if not (enabled or os.environ.get("GST_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set GST_CAPTCHA_AUTOSOLVE=1."
        )

    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to auto-solve the GST portal CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    # The <img> mounts into the DOM (and reports "visible") before its src
    # has actually finished loading pixels -- screenshotting it that early
    # produces a valid-but-blank image. naturalWidth > 0 alone is NOT
    # sufficient proof of that (see _looks_like_real_captcha's own
    # docstring for the confirmed live failure mode) -- wait for it first,
    # then poll the screenshot itself until it looks like real content.
    page.wait_for_function(
        "img => img.complete && img.naturalWidth > 0",
        arg=locator.element_handle(),
        timeout=10000,
    )
    raw_bytes = _screenshot_real_captcha(locator, page, timeout_ms=10000)
    preprocessed_bytes = _preprocess_gst_captcha_image(raw_bytes)
    return "Claude vision", _solve_with_claude_vision(preprocessed_bytes, _GST_FORMAT_HINT, "gst")


_MAHARERA_FORMAT_HINT = (
    "The text is uppercase letters (A-Z) and digits (0-9) only, rendered with wide spacing "
    "between each character. It is always uppercase even though it may look ambiguous."
)


def solve_maharera_captcha(
    page,
    enabled: bool = False,
    canvas_selector: str = _SEL_MAHARERA_CAPTCHA_CANVAS,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA <canvas> on `page` (a Playwright Page, same
    object session_auth.py already holds) and solves it via Claude vision
    on the raw screenshot. Returns (backend_name, solved_text), always
    uppercased -- this portal's CAPTCHA only ever renders UPPERCASE
    letters, confirmed live, and a correct-but-lowercase read has been
    seen rejected purely on case. Raises CaptchaSolveError/
    CaptchaSolverNotConfigured if Claude vision isn't configured or the
    call fails.

    `enabled` must be passed True explicitly (or MAHARERA_CAPTCHA_AUTOSOLVE=1
    set in the environment) -- same deliberate double gate as
    solve_gst_captcha, so autosolve can never fire just because this module
    is imported or a key happens to be set in the environment for an
    unrelated reason."""
    if not (enabled or os.environ.get("MAHARERA_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set MAHARERA_CAPTCHA_AUTOSOLVE=1."
        )

    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to auto-solve the MahaRERA CAPTCHA."
        )

    locator = page.locator(canvas_selector).first
    locator.wait_for(state="visible", timeout=15000)
    raw_bytes = locator.screenshot()
    solved_text = _solve_with_claude_vision(raw_bytes, _MAHARERA_FORMAT_HINT, "maharera")
    return "Claude vision", solved_text.upper()


# CONFIRMED LIVE 2026-10-14 against freesearchigrservice.maharashtra.gov.in's
# Document Number search: id="imgCaptcha1" -- plain colored (green) monospace
# text on a white background, no grid noise or strike-through line, same
# "deliberately human-readable" style as MahaRERA's. No preprocessing applied
# for the same reason. Only ONE example seen so far ('57732C', 6 chars) --
# not enough to confirm length/alphabet is always fixed, so the hint below
# describes the style without asserting a strict format the way GST's does.
_SEL_IGR_CAPTCHA_IMAGE = "#imgCaptcha1"
_IGR_FORMAT_HINT = (
    "The text is colored (not black), rendered in a plain font with no background noise. "
    "It may mix uppercase letters, lowercase letters, and digits -- CONFIRMED LIVE 2026-10-15 "
    "this portal's CAPTCHA is case-sensitive and does include lowercase letters, unlike "
    "MahaRERA's always-uppercase one. Read and report the EXACT case of each character; do not "
    "normalize to uppercase."
)


def solve_igr_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_IGR_CAPTCHA_IMAGE,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA image on `page` (a Playwright Page, same
    object igr_maharashtra_search.py already holds) and solves it via
    Claude vision on the raw screenshot (no preprocessing). Returns
    (backend_name, solved_text). Raises CaptchaSolveError/
    CaptchaSolverNotConfigured if Claude vision isn't configured or the
    call fails -- the caller's own retry loop (see
    igr_maharashtra_search.py's _autosolve_docno_captcha/
    _autosolve_property_captcha) is what absorbs an occasional misread.

    `enabled` must be passed True explicitly (or IGR_CAPTCHA_AUTOSOLVE=1 set
    in the environment) -- same deliberate double gate as the other
    solve_*_captcha functions, so autosolve can never fire just because
    this module is imported or a key happens to be set in the environment
    for an unrelated reason."""
    if not (enabled or os.environ.get("IGR_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set IGR_CAPTCHA_AUTOSOLVE=1."
        )

    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to auto-solve the IGR CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    raw_bytes = locator.screenshot()
    return "Claude vision", _solve_with_claude_vision(raw_bytes, _IGR_FORMAT_HINT, "igr")


# CONFIRMED LIVE 2026-10-15 against bhulekh.mahabhumi.gov.in's Property Card
# search: id="ContentPlaceHolder1_captchaImage", served as an inline
# data:image/png;base64 <img> src (not a URL) -- a live sample read cleanly
# by a human as mixed-case alphanumeric ("FaOXxs") against a scattered dot
# background, no strikethrough line or grid noise like GST's.
_SEL_MAHABHUMI_CAPTCHA_IMAGE = "#ContentPlaceHolder1_captchaImage"
_MAHABHUMI_FORMAT_HINT = (
    "The text mixes uppercase letters, lowercase letters, and digits, shown against a background "
    "of scattered dot noise (no strikethrough line or grid). Read and report the EXACT case of "
    "each character; do not normalize to uppercase or lowercase."
)


def solve_mahabhumi_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_MAHABHUMI_CAPTCHA_IMAGE,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA image on `page` (a Playwright Page, same
    object mahabhumi.py already holds) and solves it via Claude vision on
    the raw screenshot (no preprocessing -- no adversarial noise confirmed
    yet, unlike GST). Returns (backend_name, solved_text). Raises
    CaptchaSolveError/CaptchaSolverNotConfigured if Claude vision isn't
    configured or the call fails -- the caller's own retry loop (see
    mahabhumi.py's fetch_property_card) is what absorbs an occasional
    misread.

    `enabled` must be passed True explicitly (or MAHABHUMI_CAPTCHA_AUTOSOLVE=1
    set in the environment) -- same deliberate double gate as the other
    solve_*_captcha functions, so autosolve can never fire just because this
    module is imported or a key happens to be set in the environment for an
    unrelated reason."""
    if not (enabled or os.environ.get("MAHABHUMI_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set MAHABHUMI_CAPTCHA_AUTOSOLVE=1."
        )

    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to auto-solve the Mahabhumi CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    raw_bytes = locator.screenshot()
    return "Claude vision", _solve_with_claude_vision(raw_bytes, _MAHABHUMI_FORMAT_HINT, "mahabhumi")


# CONFIRMED LIVE 2026-10-15 against rerait.telangana.gov.in/SearchList/Search:
# id="captchaImage", served from a dedicated endpoint
# (/SearchList/SearchCaptcha) rather than a data: URI or a URL with a
# changing token -- the <img>'s own src string is IDENTICAL across loads
# even though the rendered content differs, so the before/after src-token
# diff IGR/Mahabhumi use to detect rejection does NOT work here. A live
# sample read cleanly by a human as uppercase letters only ("BYXSN") in a
# stylized italic serif font against a plain dotted-border background, no
# grid noise or strikethrough line.
_SEL_TS_RERA_CAPTCHA_IMAGE = "#captchaImage"
_TS_RERA_FORMAT_HINT = (
    "The text is uppercase letters only, rendered in a stylized italic serif font, shown against "
    "a plain background with a dotted border -- no grid noise or strikethrough line."
)


def solve_ts_rera_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_TS_RERA_CAPTCHA_IMAGE,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA image on `page` (a Playwright Page, same
    object ts_rera_client.py already holds) and solves it via Claude vision
    on the raw screenshot (no preprocessing -- no adversarial noise
    confirmed, unlike GST). Returns (backend_name, solved_text). Raises
    CaptchaSolveError/CaptchaSolverNotConfigured if Claude vision isn't
    configured or the call fails -- the caller's own retry loop (see
    ts_rera_client.py's search_and_fetch) is what absorbs an occasional
    misread.

    `enabled` must be passed True explicitly (or TS_RERA_CAPTCHA_AUTOSOLVE=1
    set in the environment) -- same deliberate double gate as the other
    solve_*_captcha functions, so autosolve can never fire just because this
    module is imported or a key happens to be set in the environment for an
    unrelated reason."""
    if not (enabled or os.environ.get("TS_RERA_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set TS_RERA_CAPTCHA_AUTOSOLVE=1."
        )

    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to auto-solve the TS-RERA CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    raw_bytes = locator.screenshot()
    return "Claude vision", _solve_with_claude_vision(raw_bytes, _TS_RERA_FORMAT_HINT, "ts_rera")


# CONFIRMED LIVE 2026-10-15 against www.up-rera.in/View_projects.aspx: the
# <img> has no id (ASP.NET's CaptchaImage.axd handler control renders a bare
# <img src="CaptchaImage.axd?guid=...">), so this is matched by its src
# pattern rather than an id. A live sample read as mixed-case alphanumeric
# in GREEN text ("PV9NK") against light dot noise, no strikethrough/grid --
# similar style to IGR's own colored CAPTCHA.
_SEL_UP_RERA_CAPTCHA_IMAGE = "img[src*='CaptchaImage']"
_UP_RERA_FORMAT_HINT = (
    "The text is colored (green), mixed-case alphanumeric, shown against light dot noise -- no "
    "strikethrough line or grid. Read and report the EXACT case of each character; do not "
    "normalize to uppercase or lowercase."
)


def solve_up_rera_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_UP_RERA_CAPTCHA_IMAGE,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA image on `page` (a Playwright Page, same
    object up_captcha_search.py already holds) and solves it via Claude
    vision on the raw screenshot (no preprocessing -- no adversarial noise
    confirmed, unlike GST). Returns (backend_name, solved_text). Raises
    CaptchaSolveError/CaptchaSolverNotConfigured if Claude vision isn't
    configured or the call fails -- the caller's own retry loop (see
    up_captcha_search.py) is what absorbs an occasional misread.

    `enabled` must be passed True explicitly (or UP_RERA_CAPTCHA_AUTOSOLVE=1
    set in the environment) -- same deliberate double gate as the other
    solve_*_captcha functions, so autosolve can never fire just because this
    module is imported or a key happens to be set in the environment for an
    unrelated reason."""
    if not (enabled or os.environ.get("UP_RERA_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set UP_RERA_CAPTCHA_AUTOSOLVE=1."
        )

    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to auto-solve the UP-RERA/UP-REAT CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    raw_bytes = locator.screenshot()
    return "Claude vision", _solve_with_claude_vision(raw_bytes, _UP_RERA_FORMAT_HINT, "up_rera")


# CONFIRMED LIVE 2026-10-15 against efilingreat.up.gov.in/upreat/judgement.php
# (a DIFFERENT government portal/domain than up-rera.in's own project search
# just above, with a visually distinct CAPTCHA style -- each gets its own
# dedicated solver, same convention as every other portal in this file):
# class="captcha" src="captcha.php". A live sample read as digits only
# ("67942") in yellow/orange italic text on a solid blue background box, no
# dot noise or strikethrough line.
_SEL_UP_REAT_CAPTCHA_IMAGE = "img.captcha"
_UP_REAT_FORMAT_HINT = (
    "The text is digits only (0-9), rendered in yellow/orange italic text on a solid blue "
    "background box -- no dot noise, grid, or strikethrough line."
)


def solve_up_reat_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_UP_REAT_CAPTCHA_IMAGE,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA image on `page` (a Playwright Page, same
    object up_captcha_search.py already holds) and solves it via Claude
    vision on the raw screenshot. Returns (backend_name, solved_text).
    Raises CaptchaSolveError/CaptchaSolverNotConfigured if Claude vision
    isn't configured or the call fails -- the caller's own retry loop (see
    up_captcha_search.py's search_appeals) is what absorbs an occasional
    misread.

    `enabled` must be passed True explicitly (or UP_REAT_CAPTCHA_AUTOSOLVE=1
    set in the environment) -- same deliberate double gate as the other
    solve_*_captcha functions, so autosolve can never fire just because this
    module is imported or a key happens to be set in the environment for an
    unrelated reason."""
    if not (enabled or os.environ.get("UP_REAT_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set UP_REAT_CAPTCHA_AUTOSOLVE=1."
        )

    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to auto-solve the UP-REAT CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    raw_bytes = locator.screenshot()
    return "Claude vision", _solve_with_claude_vision(raw_bytes, _UP_REAT_FORMAT_HINT, "up_reat")


_SEL_NCLT_CAPTCHA_IMAGE = "img.imgcaptcha"
_NCLT_FORMAT_HINT = (
    "The answer field accepts at most 6 alphanumeric characters. Read the exact text shown, "
    "preserve its case, and do not add spaces or punctuation."
)


def solve_nclt_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_NCLT_CAPTCHA_IMAGE,
) -> tuple[str, str]:
    """Reads NCLT's CAPTCHA image with Claude vision.

    Autosolving is deliberately opt-in via `enabled=True` or
    NCLT_CAPTCHA_AUTOSOLVE=1. Check the portal's terms before enabling this
    against live searches.
    """
    if not (enabled or os.environ.get("NCLT_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set NCLT_CAPTCHA_AUTOSOLVE=1."
        )
    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to auto-solve the NCLT CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    return "Claude vision", _solve_with_claude_vision(locator.screenshot(), _NCLT_FORMAT_HINT, "nclt")


_SEL_BOMBAY_HC_CAPTCHA_IMAGE = "#captcha_image"
_BOMBAY_HC_FORMAT_HINT = (
    "The CAPTCHA contains exactly 6 alphanumeric characters. Read the characters "
    "in their displayed case; do not add spaces or punctuation."
)


def solve_bombay_hc_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_BOMBAY_HC_CAPTCHA_IMAGE,
) -> tuple[str, str]:
    """Reads eCourts' CAPTCHA image for Bombay High Court with Claude vision.

    Autosolving is deliberately opt-in via `enabled=True` or
    BOMBAY_HC_CAPTCHA_AUTOSOLVE=1. Check the portal's terms before enabling
    this against live searches.
    """
    if not (enabled or os.environ.get("BOMBAY_HC_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set BOMBAY_HC_CAPTCHA_AUTOSOLVE=1."
        )
    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to auto-solve the Bombay HC CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    return "Claude vision", _solve_with_claude_vision(locator.screenshot(), _BOMBAY_HC_FORMAT_HINT, "bombay_hc")


def translate_indian_name(name: str, context: str = "") -> str:
    """Translates/transliterates an Indian person or company name written in
    Devanagari (or another Indian script) into the idiomatic English
    spelling an Indian person would actually use for it themselves (e.g.
    "Jalgaonkar", not an academic transliteration scheme's "jaLagAvakara").

    CONFIRMED LIVE 2026-10-15: neither a plain character-mapping fallback
    (`unidecode`) nor a dedicated transliteration library
    (`indic_transliteration`, IAST/Harvard-Kyoto schemes) reproduces this --
    both preserve the exact phonetic/orthographic form of the Devanagari
    (every schwa, every long vowel), which is NOT how these names are
    conventionally spelled in English; real spelling involves irregular
    contractions and conventions (dropped vowels, "v" vs "w", etc.) that
    aren't mechanically derivable from the script alone and were previously
    hand-translated one by one (see _translate_marathi.py's own
    PARTY_NAMES dict, built that way for the 2020-2026 Parel/Property No.
    10 IGR dataset). This automates that same judgment call via the same
    Claude text call pattern solve_*_captcha already uses for vision,
    reusing its budget cap/usage tracking (`portal="name_translation"` in
    `captcha_usage_summary()`'s own breakdown) rather than a second,
    separate spending limit.

    `context` is optional free text (e.g. "a party in a Maharashtra
    property registration document") to steer the model when the name
    alone is ambiguous. Raises CaptchaSolveError on any API failure or if
    the cross-run budget is exhausted -- same discipline as every other
    Claude call in this module, so a caller can decide whether to fall
    back to leaving the name untranslated."""
    if not name or not name.strip():
        return name
    if not _anthropic_api_key():
        raise CaptchaSolverNotConfigured(
            "ANTHROPIC_API_KEY is not set -- required to translate names via Claude."
        )
    remaining = _captcha_budget_remaining_usd()
    if remaining <= 0:
        raise CaptchaSolveError(
            f"Claude text-translation budget (${_CAPTCHA_SOLVE_COST_CAP_USD:.2f}) is exhausted "
            f"(${-remaining:.6f} over) -- see captcha_usage_summary()."
        )
    context_line = f" ({context})" if context else ""
    try:
        resp = _get_claude_client().messages.create(
            model=_CLAUDE_VISION_MODEL,
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": (
                    f"This is an Indian person or company name{context_line}, written in Devanagari "
                    f"(or another Indian script): {name!r}\n\n"
                    "Give the idiomatic English spelling an Indian person would actually use for this "
                    "name themselves -- the way it would appear on their PAN card, passport, or in "
                    "everyday English usage -- NOT an academic/phonetic transliteration scheme (so "
                    "'Jalgaonkar', not 'Jalagavakara'; 'Prabhakar', not 'Prabhakara'). Preserve the "
                    "name's full structure and level of detail without abbreviating or shortening any "
                    "part of it (e.g. 'सहकारी पतपेढी' becomes '(Co-op. Credit Society Ltd.)', not just "
                    "silently dropped; 'कुलमुखत्यार'/'मुखत्यार' becomes 'General Power of Attorney "
                    "holder'/'Power of Attorney holder', never abbreviated to 'POA'). Respond with ONLY "
                    "the English name, nothing else -- no explanation, no quotes."
                ),
            }],
        )
    except Exception as e:
        raise CaptchaSolveError(f"Claude name-translation call failed: {e}") from e
    cost_usd = _record_claude_vision_usage("name_translation", _CLAUDE_VISION_MODEL, resp)
    text = "".join(
        getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    if not text:
        raise CaptchaSolveError(f"Claude returned no text for name translation (cost=${cost_usd:.6f}).")
    return text

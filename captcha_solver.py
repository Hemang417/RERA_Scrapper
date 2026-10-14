"""
Image-CAPTCHA autosolving, used by gst_portal.py (solve_gst_captcha) and
session_auth.py (solve_maharera_captcha). Tries Claude's own vision
(claude-sonnet-5, same model/SDK pattern as deep_research.py -- these
CAPTCHAs are deliberately human-readable, not adversarial to vision, so a
multimodal LLM is a plausible match for them) FIRST, then CapSolver if
Claude isn't configured or fails, and raises past both so the caller always
has a further fallback to a human solve.

THIS IS A POLICY REVERSAL FROM EVERY OTHER CAPTCHA-GATED MODULE IN THIS
PIPELINE. Both gst_portal.py's and session_auth.py's own docstrings say
plainly that nothing there solves or reads the CAPTCHA image itself --
that policy was deliberate, not a stopgap, see guardrails.md's "HUMAN
budget" framing. Before wiring this into a live run against a government
portal, confirm this doesn't violate its Terms of Use. This module does
not decide that; it only provides the mechanism, gated behind an explicit
opt-in (see each solve_*_captcha function's `enabled` kwarg / its own
*_CAPTCHA_AUTOSOLVE env var) so it can never fire silently.

Flow (CapSolver's plain ImageToTextTask, not reCAPTCHA/hCaptcha, since the
GST portal's widget is a distorted-text image, same as every other
ASPX/Angular-era .gov.in CAPTCHA this codebase has met):
  1. Screenshot just the CAPTCHA <img> element, preprocess it (strip the
     strike-through line and grid noise, see _preprocess_gst_captcha_image).
  2. POST the image (base64) to CapSolver, get back a task id.
  3. Poll for the solved text.
  4. Caller types it into the CAPTCHA input and submits -- this module
     never touches the Search button itself, so a human watching the
     browser can always see what happened and intervene.

CONFIRMED LIVE 2026-10-14 against services.gst.gov.in/services/searchtpbypan:
  - CAPTCHA image: id="imgCaptcha" (class="captcha") -- does NOT mount into
    the DOM until the PAN/GSTIN field validates, which needs an Enter
    keypress (or blur) after filling it; see gst_portal.py's page.press(...).
  - The <img> can report "visible" before its src has actually finished
    loading pixels -- screenshotting that early produces a valid-but-blank
    image. Waiting for the element's own naturalWidth confirms real pixel
    data.
  - 2captcha was tried earlier as a second backend and DROPPED 2026-10-14:
    across every live attempt it either declined outright
    (ERROR_CAPTCHA_UNSOLVABLE) or never resolved within a reasonable poll
    window, even against the preprocessed image -- CapSolver is the only
    backend that has ever produced a usable answer against this portal's
    CAPTCHA style, so 2captcha added complexity with no observed benefit.
  - CapSolver's own accuracy against the RAW (unpreprocessed) screenshot has
    been inconsistent (roughly 1 confirmed-correct answer out of ~9 live
    attempts, several wrong by exactly one digit). _preprocess_gst_captcha_image
    was added after pixel-sampling a real captured image offline: the grid
    background turned out to be SOLID dark lines similar in brightness to
    the digit strokes (not a light tint), which a plain OCR/model pass
    can't easily separate from real ink. Two independent CapSolver calls
    against the same CAPTCHA (raw vs. preprocessed) returned the identical
    answer post-preprocessing, which is a good consistency signal -- but
    this was checked offline (submitted to CapSolver only, never to the
    live portal, to avoid adding to a WAF block this session already
    triggered), so it is NOT yet confirmed correct by an actual portal
    submission. Treat preprocessing as "should help, unconfirmed live"
    until a real trial against the portal validates it.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time

import requests
from anthropic import Anthropic
from PIL import Image, ImageFilter, ImageOps

_CAPSOLVER_CREATE_URL = "https://api.capsolver.com/createTask"
_CAPSOLVER_RESULT_URL = "https://api.capsolver.com/getTaskResult"

_CLAUDE_VISION_MODEL = "claude-sonnet-5"
_claude_client = None

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

# CONFIRMED LIVE 2026-10-14 against maharerait.maharashtra.gov.in's project
# detail page: the CAPTCHA is a <canvas id="captcahCanvas"> (that misspelling
# is the SITE's own id, not a typo introduced here) -- plain rendered text on
# a flat background, no grid noise or strike-through line like GST's, so no
# preprocessing is applied for this one. Refresh is <a class="cpt-btn">.
_SEL_MAHARERA_CAPTCHA_CANVAS = "#captcahCanvas"
_SEL_MAHARERA_CAPTCHA_REFRESH_BTN = "a.cpt-btn"

# CONFIRMED against a real captured CAPTCHA image 2026-10-14 (pixel-sampled
# offline, not against the live portal, to avoid adding to the WAF hits
# already triggered this session): the diagonal strike-through line is
# reddish (R channel clearly dominant, G/B near 0); the grid background is
# SOLID dark lines similar in brightness to the digit strokes themselves
# (not a light tint), which is almost certainly why CapSolver has struggled.
# A LOW threshold (~22, keeping only the darkest "core" pixels) isolates
# the thicker digit strokes while most of the thinner grid lines fall above
# it and disappear; a median filter then cleans up the sparse leftover
# grid-intersection dots without eating the now-solid digit shapes.
_RED_LINE_DOMINANCE = 30
_BINARIZE_THRESHOLD = 22
_UPSCALE_FACTOR = 3

_SUBMIT_POLL_INTERVAL_SECONDS = 5.0
_SUBMIT_TIMEOUT_SECONDS = 120.0


class CaptchaSolveError(Exception):
    pass


class CaptchaSolverNotConfigured(CaptchaSolveError):
    pass


def _preprocess_maharera_captcha_image(image_bytes: bytes) -> bytes:
    """Grayscale + autocontrast + upscale for MahaRERA's CAPTCHA canvas --
    NOT the grid/strike-through removal GST needs (this image has neither).
    CONFIRMED offline 2026-10-14 (against CapSolver's API directly, not the
    live portal): this alone took a wrong 5-character raw-image guess
    ('66dma') to a near-miss that got 5 of 6 characters right and in the
    correct order ('g6dmn' vs the real 'G6DMIN', only the thin 'I' dropped)
    -- a clear, if incomplete, improvement. Never raises: on any processing
    failure, returns the original bytes unchanged so CapSolver still gets
    *something* rather than nothing."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        gray = ImageOps.grayscale(img)
        gray = ImageOps.autocontrast(gray)
        upscaled = gray.resize((w * _UPSCALE_FACTOR, h * _UPSCALE_FACTOR), Image.LANCZOS)
        out = io.BytesIO()
        upscaled.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return image_bytes


def _preprocess_gst_captcha_image(image_bytes: bytes) -> bytes:
    """Strips the red strike-through line and grid-pattern noise from a raw
    GST CAPTCHA screenshot, leaving clean, bold digit shapes for the solver
    -- see the constants above for why each step is calibrated the way it
    is. Never raises: on any processing failure, returns the original bytes
    unchanged so CapSolver still gets *something* rather than nothing."""
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
        bw = gray.point(lambda v: 0 if v < _BINARIZE_THRESHOLD else 255, mode="L")
        cleaned = bw.filter(ImageFilter.MedianFilter(size=3))
        upscaled = cleaned.resize((w * _UPSCALE_FACTOR, h * _UPSCALE_FACTOR), Image.LANCZOS)
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
        _claude_client = Anthropic(http_client=httpx2.Client(verify=certifi.where()))
    return _claude_client


def _solve_with_claude_vision(image_bytes: bytes, format_hint: str, portal: str) -> str:
    """Asks Claude to read the CAPTCHA image directly via vision, the same
    model/SDK call pattern deep_research.py already uses elsewhere in this
    codebase. `format_hint` carries the portal-specific constraint (exact
    length/alphabet) the same way captcha_solver's other backends take hint
    parameters -- it goes straight into the prompt rather than a structured
    API field, since there's no separate "hint" channel for a chat model the
    way there is for CapSolver's task JSON. `portal` (e.g. "gst", "maharera")
    is just a usage-tracking label, see _record_claude_vision_usage.

    Raises CaptchaSolveError on any API failure (missing/invalid
    ANTHROPIC_API_KEY, rate limit, network error), if the response is empty
    (never silently returns a guess), or if the persisted cross-run budget
    (_CAPTCHA_SOLVE_COST_CAP_USD) is already exhausted -- checked BEFORE
    spending anything on this call, not after."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
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


def _capsolver_key() -> str:
    return os.environ.get("CAPSOLVER_API_KEY", "").strip()


def _solve_with_capsolver(
    image_bytes: bytes,
    api_key: str,
    timeout_seconds: float,
    poll_interval: float,
    task_extra: dict | None = None,
) -> str:
    """`task_extra` carries per-portal format hints (numeric/case/length) --
    see each solve_*_captcha caller for what it passes and why."""
    body_b64 = base64.b64encode(image_bytes).decode("ascii")
    task = {"type": "ImageToTextTask", "body": body_b64}
    task.update(task_extra or {})
    resp = requests.post(
        _CAPSOLVER_CREATE_URL,
        json={"clientKey": api_key, "task": task},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errorId"):
        raise CaptchaSolveError(f"CapSolver rejected the submission: {data.get('errorCode')} {data.get('errorDescription')}")

    # ImageToTextTask sometimes resolves synchronously inside createTask
    # itself (status "ready" with a solution already attached) -- check
    # before polling a task id that may not exist in that case.
    if data.get("status") == "ready" and data.get("solution"):
        return data["solution"]["text"]

    task_id = data.get("taskId")
    if not task_id:
        raise CaptchaSolveError(f"CapSolver did not return a taskId: {data!r}")

    elapsed = 0.0
    while elapsed < timeout_seconds:
        resp = requests.post(_CAPSOLVER_RESULT_URL, json={"clientKey": api_key, "taskId": task_id}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errorId"):
            raise CaptchaSolveError(f"CapSolver returned an error: {data.get('errorCode')} {data.get('errorDescription')}")
        if data.get("status") == "ready":
            return data["solution"]["text"]
        if data.get("status") != "processing":
            raise CaptchaSolveError(f"CapSolver returned an unexpected status: {data!r}")
        time.sleep(poll_interval)
        elapsed += poll_interval
    raise CaptchaSolveError(f"No solved CAPTCHA from CapSolver within {timeout_seconds}s (task id {task_id}).")


_GST_FORMAT_HINT = "The text is always exactly 6 digits (0-9 only), never letters."


def solve_gst_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_GST_CAPTCHA_IMAGE,
    submit_timeout_seconds: float = _SUBMIT_TIMEOUT_SECONDS,
    poll_interval: float = _SUBMIT_POLL_INTERVAL_SECONDS,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA image on `page` (a Playwright Page, same
    object gst_portal.py already holds) and solves it -- Claude vision
    first (on the RAW screenshot: preprocessing was tuned for CapSolver's
    simpler OCR, and a vision-capable model likely does better with full
    visual context than a stripped-down binarized image), then CapSolver
    (on the preprocessed screenshot) if Claude isn't configured or fails.
    Returns (backend_name, solved_text). Raises CaptchaSolveError/
    CaptchaSolverNotConfigured if neither backend is configured or both
    fail -- never silently falls back further than that itself, so a
    caller that catches these can decide whether to fall back to the
    human-solve wait loop.

    `enabled` must be passed True explicitly (or GST_CAPTCHA_AUTOSOLVE=1
    set in the environment) -- this is a deliberate double gate on top of
    requiring ANTHROPIC_API_KEY/CAPSOLVER_API_KEY, so autosolve can never
    fire just because this module is imported or a key happens to be set
    in the environment for an unrelated reason."""
    if not (enabled or os.environ.get("GST_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set GST_CAPTCHA_AUTOSOLVE=1."
        )

    have_claude = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    capsolver_key = _capsolver_key()
    if not have_claude and not capsolver_key:
        raise CaptchaSolverNotConfigured(
            "Neither ANTHROPIC_API_KEY nor CAPSOLVER_API_KEY is set -- required to auto-solve the GST portal CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    # The <img> mounts into the DOM (and reports "visible") before its src
    # has actually finished loading pixels -- screenshotting it that early
    # produces a valid-but-blank image. Waiting for the element's own
    # naturalWidth confirms real pixel data is in, not just that the
    # element exists.
    page.wait_for_function(
        "img => img.complete && img.naturalWidth > 0",
        arg=locator.element_handle(),
        timeout=10000,
    )
    raw_bytes = locator.screenshot()

    errors = []
    if have_claude:
        try:
            return "Claude vision", _solve_with_claude_vision(raw_bytes, _GST_FORMAT_HINT, "gst")
        except Exception as e:
            errors.append(f"Claude vision: {e}")
    if capsolver_key:
        try:
            # CONFIRMED LIVE 2026-10-14: this portal's CAPTCHA is always
            # exactly 6 digits, never letters. CapSolver does not reliably
            # honor these hints (answers have come back 4-6 characters
            # regardless), but they cost nothing to pass.
            gst_hints = {"numeric": 2, "minLength": 6, "maxLength": 6}  # 2 = pure digits (CapSolver's own enum)
            preprocessed_bytes = _preprocess_gst_captcha_image(raw_bytes)
            solved_text = _solve_with_capsolver(
                preprocessed_bytes, capsolver_key, submit_timeout_seconds, poll_interval, gst_hints
            )
            return "CapSolver", solved_text
        except Exception as e:
            errors.append(f"CapSolver: {e}")
    raise CaptchaSolveError("; ".join(errors))


_MAHARERA_FORMAT_HINT = (
    "The text is uppercase letters (A-Z) and digits (0-9) only, rendered with wide spacing "
    "between each character. It is always uppercase even though it may look ambiguous."
)


def solve_maharera_captcha(
    page,
    enabled: bool = False,
    canvas_selector: str = _SEL_MAHARERA_CAPTCHA_CANVAS,
    submit_timeout_seconds: float = _SUBMIT_TIMEOUT_SECONDS,
    poll_interval: float = _SUBMIT_POLL_INTERVAL_SECONDS,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA <canvas> on `page` (a Playwright Page, same
    object session_auth.py already holds) and solves it -- Claude vision
    first (on the RAW screenshot), then CapSolver (preprocessed: grayscale
    + autocontrast + upscale, see _preprocess_maharera_captcha_image) if
    Claude isn't configured or fails. Returns (backend_name, solved_text),
    always uppercased (see the CONFIRMED note below). Raises
    CaptchaSolveError/CaptchaSolverNotConfigured if neither backend is
    configured or both fail -- never silently falls back further than that
    itself, so a caller that catches these can decide whether to fall back
    to the human-solve wait loop.

    CONFIRMED offline/live 2026-10-14: despite how legible this image
    already looks (plain, clean mixed-case alphanumeric text on a flat
    background, no grid noise or strike-through line), raw screenshots
    were still solved wrong (e.g. wrong length) more often than not by
    CapSolver -- preprocessing measurably improved (but has not, in
    itself, guaranteed) its accuracy. Separately, this portal's CAPTCHA
    only ever renders UPPERCASE letters -- CapSolver has read the right
    characters but in lowercase at least once ('b30khg' vs. the real
    'B30KHG', an otherwise-correct solve rejected purely on case) -- so
    the result is forced uppercase here regardless of which backend
    answered, rather than trusting either one's own case handling.

    `enabled` must be passed True explicitly (or MAHARERA_CAPTCHA_AUTOSOLVE=1
    set in the environment) -- same deliberate double gate as
    solve_gst_captcha, so autosolve can never fire just because this module
    is imported or a key happens to be set in the environment for an
    unrelated reason."""
    if not (enabled or os.environ.get("MAHARERA_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set MAHARERA_CAPTCHA_AUTOSOLVE=1."
        )

    have_claude = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    capsolver_key = _capsolver_key()
    if not have_claude and not capsolver_key:
        raise CaptchaSolverNotConfigured(
            "Neither ANTHROPIC_API_KEY nor CAPSOLVER_API_KEY is set -- required to auto-solve the MahaRERA CAPTCHA."
        )

    locator = page.locator(canvas_selector).first
    locator.wait_for(state="visible", timeout=15000)
    raw_bytes = locator.screenshot()

    errors = []
    if have_claude:
        try:
            solved_text = _solve_with_claude_vision(raw_bytes, _MAHARERA_FORMAT_HINT, "maharera")
            return "Claude vision", solved_text.upper()
        except Exception as e:
            errors.append(f"Claude vision: {e}")
    if capsolver_key:
        try:
            # No length/numeric hint, since this one is NOT confirmed to
            # always be a fixed length or digits-only the way GST's is.
            maharera_hints = {"case": True}
            preprocessed_bytes = _preprocess_maharera_captcha_image(raw_bytes)
            solved_text = _solve_with_capsolver(
                preprocessed_bytes, capsolver_key, submit_timeout_seconds, poll_interval, maharera_hints
            )
            return "CapSolver", solved_text.upper()
        except Exception as e:
            errors.append(f"CapSolver: {e}")
    raise CaptchaSolveError("; ".join(errors))


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
    "It may mix uppercase letters and digits."
)


def solve_igr_captcha(
    page,
    enabled: bool = False,
    image_selector: str = _SEL_IGR_CAPTCHA_IMAGE,
    submit_timeout_seconds: float = _SUBMIT_TIMEOUT_SECONDS,
    poll_interval: float = _SUBMIT_POLL_INTERVAL_SECONDS,
) -> tuple[str, str]:
    """Screenshots the CAPTCHA image on `page` (a Playwright Page, same
    object igr_maharashtra_search.py already holds) and solves it -- Claude
    vision first, then CapSolver (no preprocessing; see the module note
    above for why) if Claude isn't configured or fails. Returns
    (backend_name, solved_text). Raises CaptchaSolveError/
    CaptchaSolverNotConfigured if neither backend is configured or both
    fail -- never silently falls back further than that itself, so a
    caller that catches these can decide whether to fall back to the
    human-solve wait loop.

    `enabled` must be passed True explicitly (or IGR_CAPTCHA_AUTOSOLVE=1 set
    in the environment) -- same deliberate double gate as the other
    solve_*_captcha functions, so autosolve can never fire just because
    this module is imported or a key happens to be set in the environment
    for an unrelated reason."""
    if not (enabled or os.environ.get("IGR_CAPTCHA_AUTOSOLVE") == "1"):
        raise CaptchaSolveError(
            "CAPTCHA autosolve is not enabled -- pass enabled=True or set IGR_CAPTCHA_AUTOSOLVE=1."
        )

    have_claude = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    capsolver_key = _capsolver_key()
    if not have_claude and not capsolver_key:
        raise CaptchaSolverNotConfigured(
            "Neither ANTHROPIC_API_KEY nor CAPSOLVER_API_KEY is set -- required to auto-solve the IGR CAPTCHA."
        )

    locator = page.locator(image_selector).first
    locator.wait_for(state="visible", timeout=15000)
    raw_bytes = locator.screenshot()

    errors = []
    if have_claude:
        try:
            return "Claude vision", _solve_with_claude_vision(raw_bytes, _IGR_FORMAT_HINT, "igr")
        except Exception as e:
            errors.append(f"Claude vision: {e}")
    if capsolver_key:
        try:
            solved_text = _solve_with_capsolver(raw_bytes, capsolver_key, submit_timeout_seconds, poll_interval, {})
            return "CapSolver", solved_text
        except Exception as e:
            errors.append(f"CapSolver: {e}")
    raise CaptchaSolveError("; ".join(errors))

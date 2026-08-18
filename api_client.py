"""
Talks to MahaRERA's public (no-auth) project data API.
"""

import concurrent.futures
import json
import os
import re
import shutil
import threading
from urllib.parse import urlparse

import requests

import config


def _headers(token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class CategoryFetchError(Exception):
    def __init__(self, category: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code


_ENVELOPE_ONLY_KEYS = {
    "message", "status", "responseCode", "responseMessage", "totalCount", "responseObject", "items",
    "data", "content", "projects",
}


def _unwrap_envelope(data):
    """MahaRERA's various microservices wrap payloads inconsistently -- try
    the known envelope keys before falling back to the raw body.

    Some endpoints return HTTP 200 with a body that's just bookkeeping and no
    real payload -- e.g. {"message": "ERROR", "status": "0", "responseObject":
    null} or {"message": "No records found", "status": "NO_RECORDS_FOUND", ...}
    -- confirmed live on spocs/past_experiences/sro_details. Without this
    check that bookkeeping gets displayed as if it were real data (a
    "message"/"status" row in the PDF). If every key present is just
    generic envelope bookkeeping and none of the known payload keys had a
    real value, treat it as an empty result instead."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("responseObject", "data", "projects", "content"):
            if data.get(key) is not None:
                return data[key]
        if data and set(data.keys()) <= _ENVELOPE_ONLY_KEYS:
            return {}
    return data


def fetch_category(
    category: str,
    project_id: str,
    session: requests.Session,
    token: str | None = None,
    body: dict | None = None,
    endpoints: dict | None = None,
    base_url: str | None = None,
):
    """Calls the configured endpoint for one category, returns the unwrapped body.
    Every category takes {"projectId": ...} except where the caller passes an
    explicit `body` override (see past_experiences in fetch_all_categories).

    `endpoints`/`base_url` default to MahaRERA's tables in config, so every
    existing caller is unchanged. They exist so a second state's adapter can
    supply its own endpoint table without this module having to know that
    states exist at all."""
    endpoints = endpoints if endpoints is not None else config.CATEGORY_ENDPOINTS
    base_url = base_url if base_url is not None else config.BASE_URL
    endpoint = endpoints[category]
    url = base_url + endpoint["path"]
    if body is None:
        body = {"projectId": project_id}

    try:
        resp = session.post(url, headers=_headers(token), json=body, timeout=config.REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise CategoryFetchError(category, f"network error: {e}") from e

    if resp.status_code != 200:
        raise CategoryFetchError(category, f"HTTP {resp.status_code}", resp.status_code)

    try:
        data = resp.json()
    except ValueError as e:
        raise CategoryFetchError(category, "200 OK but not valid JSON") from e

    return _unwrap_envelope(data)


def _past_experiences_body(
    project_id: str, results: dict, session: requests.Session, token: str | None
) -> dict:
    """getPromoterPastExpProject rejects the generic {"projectId": ...} body
    with HTTP 400 (userProfileId: rejected value [null]) -- it keys off the
    promoter's userProfileId instead (confirmed: {"userProfileId": ...} alone
    returns 200; adding projectId back in is harmless but not required).
    userProfileId lives on the 'projects' category's response, so reuse it if
    already fetched this run, otherwise fetch it fresh (no-auth, cheap)."""
    projects_data = results.get("projects")
    if not isinstance(projects_data, dict):
        projects_data = fetch_category("projects", project_id, session, token)
    user_profile_id = projects_data.get("userProfileId") if isinstance(projects_data, dict) else None
    return {"userProfileId": user_profile_id, "projectId": project_id}


def _fetch_and_save_category(category: str, project_id: str, raw_dir: str, token: str | None, results_so_far: dict):
    """Runs in its own thread with its own requests.Session (see
    download_documents' module note on why sessions are never shared
    across threads here). Returns (category, data, error) -- error is None
    on success -- rather than raising, so a future's .result() never
    throws and one category's failure can't affect any other's."""
    raw_path = os.path.join(raw_dir, f"{category}.json")
    try:
        with requests.Session() as session:
            if category == "past_experiences":
                body = _past_experiences_body(project_id, results_so_far, session, token)
                data = fetch_category(category, project_id, session, token, body=body)
            else:
                data = fetch_category(category, project_id, session, token)
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return category, data, None
    except CategoryFetchError as e:
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump({"_error": str(e), "status_code": e.status_code}, f, indent=2)
        return category, None, e


def fetch_all_categories(
    project_id: str,
    raw_dir: str,
    token: str | None = None,
    categories: list[str] | None = None,
    errors_out: dict | None = None,
    endpoints: dict | None = None,
    category_order: list | None = None,
    base_url: str | None = None,
) -> dict:
    """Fetches the given categories (defaults to every configured category),
    writes raw JSON for each to raw_dir, and returns {category: data_or_None}.
    One category's failure does not abort the others.

    "projects" is always fetched first, alone: "past_experiences" needs its
    userProfileId (see _past_experiences_body), the one real ordering
    dependency among these categories. Every other category hits a
    different microservice endpoint and doesn't depend on any of the
    others, so they're all fetched CONCURRENTLY rather than one after
    another -- this was the other half of a real, measured bottleneck
    (alongside the Phase 2 checks and MahaRERA judgments search) in a full
    pipeline run.

    If `errors_out` is given, it's populated {category: CategoryFetchError}
    for every failure so a caller can inspect e.g. status_code to decide
    whether a fresh auth session would help (see main.py's retry logic)."""
    os.makedirs(raw_dir, exist_ok=True)
    results = {}
    categories = categories if categories is not None else config.CATEGORY_ORDER

    def _record(category, data, error):
        results[category] = data
        if error is None:
            print(f"  [OK] {category}: fetched")
            return
        if errors_out is not None:
            errors_out[category] = error
        status = config.CATEGORY_ENDPOINTS[category]["status"]
        print(f"  [WARN] {category}: failed ({error}) -- endpoint status='{status}'")

    remaining = list(categories)
    if "projects" in remaining:
        remaining.remove("projects")
        _record(*_fetch_and_save_category("projects", project_id, raw_dir, token, results))

    if remaining:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(remaining)) as executor:
            futures = [
                executor.submit(_fetch_and_save_category, category, project_id, raw_dir, token, results)
                for category in remaining
            ]
            for future in futures:
                _record(*future.result())

    return results


_URL_KEY_HINTS = ("url", "link", "path", "file")
# documentDmsRefNo/documentFileName are the real, confirmed field names on
# getUploadedDocuments records; the rest are speculative aliases kept as a
# fallback in case other categories/older records use different naming.
_DOC_ID_KEYS = ("documentDmsRefNo", "documentId", "docId", "id")
_DOC_FILENAME_KEYS = ("documentFileName", "fileName", "docName", "documentName", "name")


def _find_document_refs(data) -> tuple[list[dict], list[dict]]:
    """Walks an unknown-shaped documents payload looking for
    {documentId, fileName} pairs (the real download mechanism -- see
    config.DMS_DOWNLOAD_PATH) as well as any plain URL-like strings
    (fallback, for shapes that don't match). Returns (doc_refs, url_refs)."""
    doc_refs = []
    url_refs = []

    def walk(node, label_hint=""):
        if isinstance(node, dict):
            label = (
                node.get("documentDescription")
                or node.get("documentDetails")
                or node.get("documentFileName")
                or node.get("documentName")
                or node.get("name")
                or node.get("fileName")
                or label_hint
            )

            doc_id = next((node[k] for k in _DOC_ID_KEYS if node.get(k) not in (None, "")), None)
            file_name = next((node[k] for k in _DOC_FILENAME_KEYS if node.get(k) not in (None, "")), None)
            if doc_id is not None and file_name is not None:
                doc_refs.append({"label": str(label or file_name), "documentId": doc_id, "fileName": file_name})

            for k, v in node.items():
                if isinstance(v, str) and (
                    any(h in k.lower() for h in _URL_KEY_HINTS) or v.startswith("http")
                ):
                    if v.startswith("http") or v.startswith("/"):
                        url_refs.append({"label": str(label or k), "url": v})
                elif isinstance(v, (dict, list)):
                    walk(v, label)
        elif isinstance(node, list):
            for item in node:
                walk(item, label_hint)

    walk(data)
    return doc_refs, url_refs


def _filename_from_response(resp, fallback: str, index: int) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    match = re.search(r'filename="?([^"\;]+)"?', cd)
    if match:
        return match.group(1).strip()
    if fallback:
        tail = os.path.basename(urlparse(fallback).path) or fallback
        if tail:
            return tail
    return f"document_{index}"


def _dedupe_filename(filename: str, seen: set) -> str:
    """MahaRERA's DMS can give two genuinely different documents (different
    document_id) the exact same filename -- confirmed live, e.g. two
    unrelated records both named "Annexure - amenities.pdf". Saving both to
    dest_dir/<filename> would let the second silently overwrite the first.
    Returns filename unchanged if not already in `seen`, otherwise appends a
    counter suffix before the extension. Caller must add the returned name
    to `seen` once it's actually used."""
    if filename not in seen:
        return filename
    base, ext = os.path.splitext(filename)
    counter = 2
    while f"{base} ({counter}){ext}" in seen:
        counter += 1
    return f"{base} ({counter}){ext}"


def _sniff_and_save(resp, dest_path: str, session: requests.Session, headers: dict) -> bool:
    """Saves resp's body to dest_path. The DMS endpoint's Content-Type header
    isn't trustworthy -- it's been observed labelling raw PDF bytes as
    "application/json" -- so JSON is only trusted if the body actually
    parses as JSON. If it does and wraps a nested download URL rather than
    file bytes, that URL is followed instead. Otherwise (parse fails, or no
    recognizable nested URL), the raw bytes are what's saved. Returns True on
    success."""
    try:
        payload = resp.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        nested_url = None
        for key in ("url", "downloadUrl", "signedUrl", "data"):
            val = payload.get(key)
            if isinstance(val, str) and val.startswith("http"):
                nested_url = val
                break
            if isinstance(val, dict):
                for inner_key in ("url", "downloadUrl", "signedUrl"):
                    if isinstance(val.get(inner_key), str):
                        nested_url = val[inner_key]
                        break
            if nested_url:
                break

        if not nested_url:
            # Parsed as JSON but not a shape we recognize -- not the file.
            return False

        follow = session.get(nested_url, headers=headers, timeout=config.REQUEST_TIMEOUT, stream=True)
        if follow.status_code != 200:
            return False
        with open(dest_path, "wb") as f:
            for chunk in follow.iter_content(chunk_size=8192):
                f.write(chunk)
        return True

    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return True


_MAX_DOCUMENT_DOWNLOAD_WORKERS = 8


def download_documents(
    documents_data,
    dest_dir: str,
    token: str | None = None,
    project_id: str | None = None,
    prior_manifest: list[dict] | None = None,
    prior_documents_dir: str | None = None,
) -> list[dict]:
    """Downloads every document referenced in the documents category payload
    into dest_dir. Prefers the real documentId/fileName DMS download
    endpoint; falls back to GETting any plain URL-like reference found for
    payload shapes that don't match. Returns a manifest list including
    failures, each tagged with which mechanism served it.

    Downloads run concurrently (capped at _MAX_DOCUMENT_DOWNLOAD_WORKERS)
    rather than one at a time -- a project can have 100+ documents, and
    each is an independent request no matter which mechanism serves it.
    Every worker uses its own fresh requests.Session() (never one shared
    across threads -- requests.Session's thread-safety isn't guaranteed by
    the library, and the connection-reuse benefit of sharing one here is
    marginal next to that risk). The one piece of state genuinely shared
    across documents -- `seen_filenames`, since MahaRERA's DMS can give two
    different documents the identical filename -- is guarded by a lock so
    concurrent downloads can never claim the same on-disk name.

    If prior_manifest/prior_documents_dir are given (a previous run's
    manifest plus its now-archived documents/ folder -- see
    run_archive.py), a document already downloaded successfully under the
    same document_id/source_filename (or the same URL, for the direct-URL
    fallback) is copied over from there instead of re-hitting MahaRERA's
    slow DMS server, and tagged status="reused" rather than "downloaded"."""
    os.makedirs(dest_dir, exist_ok=True)

    if not documents_data:
        return []

    doc_refs, url_refs = _find_document_refs(documents_data)
    dms_url = config.BASE_URL + config.DMS_DOWNLOAD_PATH

    prior_by_doc_key, prior_by_url = {}, {}
    if prior_manifest and prior_documents_dir:
        for prior_entry in prior_manifest:
            if prior_entry.get("status") not in ("downloaded", "reused") or not prior_entry.get("saved_filename"):
                continue
            if prior_entry.get("method") == "dms-post" and prior_entry.get("document_id") is not None:
                prior_by_doc_key[(prior_entry["document_id"], prior_entry.get("source_filename"))] = prior_entry
            elif prior_entry.get("method") == "direct-url" and prior_entry.get("original_url"):
                prior_by_url[prior_entry["original_url"]] = prior_entry

    seen_filenames = set()
    seen_urls = set()
    claim_lock = threading.Lock()

    def _claim_filename(candidate_name: str) -> str:
        """Atomically dedupes `candidate_name` against every name claimed
        so far (by any thread) and reserves the result -- the
        check-then-add here is the one part of this function that
        genuinely needs to be a single critical section under concurrency."""
        with claim_lock:
            filename = _dedupe_filename(candidate_name, seen_filenames)
            seen_filenames.add(filename)
            return filename

    def _claim_url_once(url: str) -> bool:
        """Returns True the first time `url` is seen, False on every
        subsequent (duplicate) occurrence -- same locking reasoning as
        _claim_filename, just for the direct-URL de-duplication check."""
        with claim_lock:
            if url in seen_urls:
                return False
            seen_urls.add(url)
            return True

    def _reuse_from_prior(prior_entry: dict) -> str | None:
        """Returns the (possibly deduped) filename actually saved, or None
        on failure."""
        prior_path = os.path.join(prior_documents_dir, prior_entry["saved_filename"])
        if not os.path.isfile(prior_path):
            return None
        filename = _claim_filename(prior_entry["saved_filename"])
        shutil.copy2(prior_path, os.path.join(dest_dir, filename))
        return filename

    def _download_dms_document(i: int, ref: dict) -> dict:
        entry = {
            "label": ref["label"],
            "original_url": dms_url,
            "saved_filename": None,
            "status": "failed",
            "method": "dms-post",
            "document_id": ref["documentId"],
            "source_filename": ref["fileName"],
        }

        prior = prior_by_doc_key.get((ref["documentId"], ref["fileName"]))
        if prior:
            reused_filename = _reuse_from_prior(prior)
            if reused_filename:
                entry["saved_filename"] = reused_filename
                entry["status"] = "reused"
                return entry

        headers = _headers(token)
        headers["Origin"] = config.BASE_URL
        if project_id:
            headers["Referer"] = config.BASE_URL + f"/public/project/view/{project_id}"

        try:
            with requests.Session() as session:
                resp = session.post(
                    dms_url,
                    headers=headers,
                    json={"fileName": ref["fileName"], "documentId": ref["documentId"]},
                    timeout=config.REQUEST_TIMEOUT,
                    stream=True,
                )
                if resp.status_code == 200:
                    filename = _claim_filename(_filename_from_response(resp, str(ref["fileName"]), i))
                    dest_path = os.path.join(dest_dir, filename)
                    if _sniff_and_save(resp, dest_path, session, headers):
                        entry["saved_filename"] = filename
                        entry["status"] = "downloaded"
                    else:
                        entry["status"] = "failed (couldn't resolve file bytes from response)"
                else:
                    entry["status"] = f"failed (HTTP {resp.status_code})"
        except requests.RequestException as e:
            entry["status"] = f"failed ({e})"

        return entry

    def _download_url_document(i: int, ref: dict) -> dict | None:
        url = ref["url"]
        if not _claim_url_once(url):
            return None  # already processed this exact URL from another ref

        full_url = url if url.startswith("http") else config.BASE_URL + url
        entry = {
            "label": ref["label"],
            "original_url": full_url,
            "saved_filename": None,
            "status": "failed",
            "method": "direct-url",
        }

        prior = prior_by_url.get(full_url)
        if prior:
            reused_filename = _reuse_from_prior(prior)
            if reused_filename:
                entry["saved_filename"] = reused_filename
                entry["status"] = "reused"
                return entry

        try:
            with requests.Session() as session:
                resp = session.get(full_url, headers=_headers(token), timeout=config.REQUEST_TIMEOUT, stream=True)
                if resp.status_code == 200:
                    filename = _claim_filename(_filename_from_response(resp, full_url, i))
                    dest_path = os.path.join(dest_dir, filename)
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    entry["saved_filename"] = filename
                    entry["status"] = "downloaded"
                else:
                    entry["status"] = f"failed (HTTP {resp.status_code})"
        except requests.RequestException as e:
            entry["status"] = f"failed ({e})"

        return entry

    manifest = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_DOCUMENT_DOWNLOAD_WORKERS) as executor:
        futures = [executor.submit(_download_dms_document, i, ref) for i, ref in enumerate(doc_refs, start=1)]
        futures += [executor.submit(_download_url_document, i, ref) for i, ref in enumerate(url_refs, start=1)]
        for future in futures:
            result = future.result()
            if result is not None:
                manifest.append(result)

    return manifest


def download_complaint_orders(
    complaints_data,
    dest_dir: str,
    token: str | None = None,
    project_id: str | None = None,
    prior_manifest: list[dict] | None = None,
    prior_documents_dir: str | None = None,
) -> list[dict]:
    """Downloads the order/judgment PDF already referenced on each individual
    complaint record (orderDmsRefNo + orderFileName, present on both
    complaintDetails[] and miscComplaintDetails[]) -- confirmed live: this
    reference has always been present in complaints.json, it was simply
    never downloaded before. Same DMS mechanism as download_documents()
    (reuses _dedupe_filename/_filename_from_response/_sniff_and_save rather
    than duplicating that logic), kept as a separate function because the
    input shape here is a known, fixed complaints.json structure rather than
    the generic documents-category payload download_documents() walks.

    Each manifest entry also carries complaint_id/complaint_registration_no
    so a later pass (extracting the actual outcome from the downloaded PDF)
    can attribute it back to the specific complaint it belongs to. Same
    prior_manifest/prior_documents_dir reuse pattern as download_documents."""
    os.makedirs(dest_dir, exist_ok=True)
    manifest = []
    if not complaints_data:
        return manifest

    refs = []
    for section in ("complaintDetails", "miscComplaintDetails"):
        for c in (complaints_data.get(section) or []):
            order_ref_no = c.get("orderDmsRefNo")
            order_file_name = c.get("orderFileName")
            if order_ref_no and order_file_name:
                refs.append({
                    "complaint_id": c.get("complaintId"),
                    "complaint_registration_no": c.get("complaintRegistrationNo"),
                    "documentId": order_ref_no,
                    "fileName": order_file_name,
                    "label": f"Order for complaint {c.get('complaintRegistrationNo') or c.get('complaintId')}",
                })

    dms_url = config.BASE_URL + config.DMS_DOWNLOAD_PATH

    prior_by_doc_key = {}
    if prior_manifest and prior_documents_dir:
        for prior_entry in prior_manifest:
            if prior_entry.get("status") not in ("downloaded", "reused") or not prior_entry.get("saved_filename"):
                continue
            if prior_entry.get("document_id") is not None:
                prior_by_doc_key[(prior_entry["document_id"], prior_entry.get("source_filename"))] = prior_entry

    seen_filenames = set()

    def _reuse_from_prior(prior_entry: dict) -> str | None:
        prior_path = os.path.join(prior_documents_dir, prior_entry["saved_filename"])
        if not os.path.isfile(prior_path):
            return None
        filename = _dedupe_filename(prior_entry["saved_filename"], seen_filenames)
        shutil.copy2(prior_path, os.path.join(dest_dir, filename))
        return filename

    with requests.Session() as session:
        for i, ref in enumerate(refs, start=1):
            entry = {
                "label": ref["label"],
                "complaint_id": ref["complaint_id"],
                "complaint_registration_no": ref["complaint_registration_no"],
                "original_url": dms_url,
                "saved_filename": None,
                "status": "failed",
                "method": "dms-post",
                "document_id": ref["documentId"],
                "source_filename": ref["fileName"],
            }

            prior = prior_by_doc_key.get((ref["documentId"], ref["fileName"]))
            if prior:
                reused_filename = _reuse_from_prior(prior)
                if reused_filename:
                    entry["saved_filename"] = reused_filename
                    entry["status"] = "reused"
                    seen_filenames.add(reused_filename)
                    manifest.append(entry)
                    continue

            headers = _headers(token)
            headers["Origin"] = config.BASE_URL
            if project_id:
                headers["Referer"] = config.BASE_URL + f"/public/project/view/{project_id}"

            try:
                resp = session.post(
                    dms_url,
                    headers=headers,
                    json={"fileName": ref["fileName"], "documentId": ref["documentId"]},
                    timeout=config.REQUEST_TIMEOUT,
                    stream=True,
                )
                if resp.status_code == 200:
                    filename = _dedupe_filename(_filename_from_response(resp, str(ref["fileName"]), i), seen_filenames)
                    dest_path = os.path.join(dest_dir, filename)
                    if _sniff_and_save(resp, dest_path, session, headers):
                        entry["saved_filename"] = filename
                        entry["status"] = "downloaded"
                        seen_filenames.add(filename)
                    else:
                        entry["status"] = "failed (couldn't resolve file bytes from response)"
                else:
                    entry["status"] = f"failed (HTTP {resp.status_code})"
            except requests.RequestException as e:
                entry["status"] = f"failed ({e})"

            manifest.append(entry)

    return manifest

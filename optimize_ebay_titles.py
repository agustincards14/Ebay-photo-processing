"""Phase 2 Gemini AI Title Optimizer for eBay Vintage Photograph Listings.

Generates Cassini-optimized 70-80 character titles using Gemini 3.8 Flash
with strict validation, deterministic local caching, diverse sampling, and
sold-safe live bulk pushing (opt-in via --push-live).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from ebay_oauth_helper import ENVIRONMENTS
from optimize_ebay_listings import (
    ANTIQUE_CUTOFF_YEAR,
    _load_audit_records,
    _push_bulk_inventory,
    _push_single_inventory,
    build_html_description,
    extract_year_int,
)
from sync_ebay_live_status import (
    TARGET_EPSCAN_DIRS,
    TokenManager,
    classify_live_item,
    fetch_bulk_inventory,
    fetch_single_offer,
)

DEFAULT_TITLE_MODEL = "gemini-3.8-flash"
TITLE_PROMPT_VERSION = 2

STOP_WORDS = {"in", "on", "of", "at", "and", "&", "with", "by", "to", "a", "the", "for", "from"}
FORBIDDEN_SPAM = ("l@@k", "wow", "rare", "mint")


class RewrittenTitle(BaseModel):
    sku: str = Field(description="Exact SKU provided in the input batch.")
    title: str = Field(description="SEO-optimized eBay title strictly <= 80 characters.")


class TitleRewriteBatch(BaseModel):
    items: list[RewrittenTitle] = Field(description="One rewritten title per input SKU.")


_client = None


def get_genai_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable is not set.")
        _client = genai.Client(api_key=api_key)
    return _client


def _era_and_decade(meta: dict[str, Any]) -> tuple[str, Optional[str], Optional[int]]:
    year_int = extract_year_int(meta.get("year"))
    era_tag = "Antique" if (year_int is not None and year_int <= ANTIQUE_CUTOFF_YEAR) else "Vintage"
    decade = f"{(year_int // 10) * 10}s" if year_int is not None else None
    return era_tag, decade, year_int


def validate_seo_title(title: str, meta: dict[str, Any]) -> list[str]:
    """Validate an AI-generated eBay title against Cassini SEO & grammar rules."""
    errors: list[str] = []
    t = " ".join((title or "").split())
    if not t:
        return ["Title is empty."]

    if len(t) < 45:
        errors.append(f"Too short ({len(t)} chars; minimum 45).")
    if len(t) > 80:
        errors.append(f"Too long ({len(t)} chars; maximum 80).")

    if " - " not in t:
        errors.append("Missing ' - ' separator before the era/format suffix.")
    if t.count(" - ") > 1:
        errors.append("Use at most one ' - ' separator.")
    if t.count(",") > 2:
        errors.append("Too many commas (maximum 2).")
    if "#" in t:
        errors.append("Contains '#'; hashtags are not allowed in eBay titles.")
    if "..." in t or "…" in t:
        errors.append("Contains ellipsis ('...' or '…').")
    if '"' in t:
        errors.append("Contains double quotes.")

    tl = t.lower()
    for spam in FORBIDDEN_SPAM:
        if re.search(rf"\b{re.escape(spam)}\b", tl) or (spam == "l@@k" and "l@@k" in tl):
            errors.append(f"Contains forbidden spam word '{spam}'.")

    if "black & white" in tl or "black and white" in tl:
        errors.append("Use 'B&W' instead of 'Black & White' / 'Black and White'.")

    if "photo" not in tl:
        errors.append("Missing required word 'Photo'.")

    era_tag, decade, _ = _era_and_decade(meta)
    if era_tag.lower() not in tl:
        errors.append(f"Missing required era word '{era_tag}'.")
    if decade and decade.lower() not in tl:
        errors.append(f"Missing required decade '{decade}'.")

    # Duplicate word check (ignoring short connectors)
    words = [re.sub(r"^[^\w&]+|[^\w&]+$", "", w.lower()) for w in t.split()]
    seen: set[str] = set()
    dups: list[str] = []
    for w in words:
        if not w or w in STOP_WORDS or len(w) <= 1:
            continue
        if w in seen:
            dups.append(w)
        seen.add(w)
    if dups:
        errors.append(f"Contains duplicate word(s): {', '.join(sorted(set(dups)))}.")

    return errors


def _source_payload_for_item(sku: str, current_title: str, meta: dict[str, Any]) -> dict[str, Any]:
    era_tag, decade, year_int = _era_and_decade(meta)
    return {
        "sku": sku,
        "current_title": current_title,
        "year": str(year_int) if year_int is not None else str(meta.get("year") or ""),
        "decade": decade or "",
        "era_tag": era_tag,
        "subjects": meta.get("subjects") or [],
        "themes": meta.get("themes") or [],
        "location": meta.get("location") or "",
        "image_color": meta.get("image_color") or "Black & White",
        "photo_type": meta.get("photo_type") or "Snapshot",
        "back_text_transcription": meta.get("back_text_transcription") or "",
        "description": meta.get("description") or "",
    }


def compute_title_cache_key(source_item: dict[str, Any], model: str) -> str:
    identity = {
        "version": TITLE_PROMPT_VERSION,
        "model": model,
        "source": source_item,
    }
    raw = json.dumps(identity, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def get_cached_title(
    cache_dir: Path, sku: str, cache_key: str
) -> Optional[str]:
    safe_sku = re.sub(r"[^A-Za-z0-9_\-]", "_", sku)
    p = cache_dir / f"{safe_sku}_{cache_key}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("title")
    except Exception:
        return None


def save_cached_title(
    cache_dir: Path, sku: str, cache_key: str, title: str, model: str
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_sku = re.sub(r"[^A-Za-z0-9_\-]", "_", sku)
    p = cache_dir / f"{safe_sku}_{cache_key}.json"
    p.write_text(
        json.dumps(
            {
                "sku": sku,
                "title": title,
                "model": model,
                "version": TITLE_PROMPT_VERSION,
                "cache_key": cache_key,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def generate_titles_for_batch(
    batch_items: list[tuple[dict[str, Any], dict[str, Any]]],
    model: str = DEFAULT_TITLE_MODEL,
    retry_feedback: Optional[dict[str, list[str]]] = None,
) -> dict[str, str]:
    """Call Gemini with structured JSON output for up to 25 items."""
    client = get_genai_client()
    prompt_items = []
    for src, _meta in batch_items:
        entry = dict(src)
        if retry_feedback and src["sku"] in retry_feedback:
            entry["previous_validation_errors"] = retry_feedback[src["sku"]]
        prompt_items.append(entry)

    prompt = (
        "You are an expert eBay vintage photograph SEO specialist writing clean, natural, high-converting titles for eBay's Cassini search engine.\n"
        "Rewrite the title for each item below. STRICT RULES:\n"
        "1. Length: STRICTLY <= 80 characters (target 68 to 80 characters).\n"
        "2. Grammar & Format: Use natural, scannable phrasing with ', ' and ' - ' separating subject/details from the era/format suffix:\n"
        "   Pattern: <Main Subject / Name / Location>, <Visual / Setting Details> - <Vintage/Antique> <Decade> <B&W/Sepia/Color> Snapshot Photo\n"
        "   Gold examples:\n"
        "   - Nina McCully, Family Garden Foliage - Antique 1920s Sepia Snapshot Photo\n"
        "   - Farmer Harnessed Mules, Agriculture - Antique 1920s Sepia Snapshot Photo\n"
        "   - Baton Rouge Women, Cloche Hats Rocks - Antique 1920s Sepia Snapshot Photo\n"
        "3. Do NOT include the word 'Original' (save characters for descriptive subject/setting/location terms).\n"
        "4. NO quotes, NO '#', NO '...', NO spam (L@@K, WOW, RARE, MINT), NO duplicate words, at most 2 commas and exactly one ' - '.\n"
        "5. Front-load the main subject with rich visual/setting details strictly grounded in the provided metadata (do NOT invent details).\n"
        "6. Include specific city/state/landmark if known (skip generic 'United States'/'USA') and notable back-of-photo names/places/stamps if distinctive.\n"
        "7. Every title MUST include:\n"
        "   - Exactly one ' - ' separator before the era/format suffix\n"
        "   - The item's exact `era_tag` ('Vintage' or 'Antique')\n"
        "   - The item's `decade` (e.g. '1950s', if non-empty)\n"
        "   - Color: 'B&W' (NEVER 'Black & White' or 'Black and White'), 'Sepia', or 'Color'\n"
        "   - Format: 'Snapshot' (or `photo_type`) and the word 'Photo'.\n"
        "8. If `previous_validation_errors` is present, fix every listed issue.\n\n"
        f"Items JSON:\n{json.dumps(prompt_items, ensure_ascii=False)}"
    )

    resp = None
    last_exc: Optional[Exception] = None
    for retry in range(4):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TitleRewriteBatch,
                    temperature=0.3,
                ),
            )
            break
        except Exception as exc:
            last_exc = exc
            if retry < 3:
                wait_s = 2 ** (retry + 1)
                time.sleep(wait_s)
            else:
                raise RuntimeError(f"Gemini API failed after retries: {last_exc}") from exc

    if resp is None or not resp.text:
        raise RuntimeError("Empty response from Gemini API.")
    parsed = json.loads(resp.text)
    out: dict[str, str] = {}
    for item in parsed.get("items", []):
        sku = item.get("sku")
        title = " ".join((item.get("title") or "").split())
        if sku and title:
            out[sku] = title
    return out


def generate_and_cache_titles(
    items_with_meta: list[tuple[dict[str, Any], dict[str, Any]]],
    cache_dir: Path,
    model: Optional[str] = None,
    max_attempts: int = 3,
    workers: int = 5,
    failed_out: Optional[dict[str, list[str]]] = None,
) -> dict[str, str]:
    """Generate, validate, and cache AI titles for (audit_rec, meta) pairs."""
    model_name = model or os.environ.get("EBAY_TITLE_MODEL", DEFAULT_TITLE_MODEL)
    results: dict[str, str] = {}
    pending: list[tuple[dict[str, Any], dict[str, Any], str]] = []

    for rec, meta in items_with_meta:
        sku = rec["sku"]
        cur_title = rec.get("live_title") or meta.get("title") or ""
        src = _source_payload_for_item(sku, cur_title, meta)
        ckey = compute_title_cache_key(src, model_name)
        cached = get_cached_title(cache_dir, sku, ckey)
        if cached and not validate_seo_title(cached, meta):
            results[sku] = cached
        else:
            pending.append((src, meta, ckey))

    if not pending:
        return results

    chunks = [pending[i : i + 25] for i in range(0, len(pending), 25)]
    total_chunks = len(chunks)
    lock = threading.Lock()

    def _process_chunk(chunk_idx: int, chunk: list[tuple[dict[str, Any], dict[str, Any], str]]) -> tuple[int, dict[str, str], dict[str, list[str]]]:
        remaining = list(chunk)
        feedback: dict[str, list[str]] = {}
        chunk_ok: dict[str, str] = {}

        for attempt in range(1, max_attempts + 1):
            if not remaining:
                break
            batch_input = [(src, meta) for src, meta, _ in remaining]
            try:
                generated = generate_titles_for_batch(
                    batch_input, model=model_name, retry_feedback=feedback or None
                )
            except Exception as exc:
                for src, _, _ in remaining:
                    feedback[src["sku"]] = [f"API error: {exc}"]
                break

            next_remaining: list[tuple[dict[str, Any], dict[str, Any], str]] = []
            new_feedback: dict[str, list[str]] = {}

            for src, meta, ckey in remaining:
                sku = src["sku"]
                cand = generated.get(sku, "")
                errs = validate_seo_title(cand, meta)
                if not errs:
                    save_cached_title(cache_dir, sku, ckey, cand, model_name)
                    chunk_ok[sku] = cand
                else:
                    new_feedback[sku] = errs
                    next_remaining.append((src, meta, ckey))

            remaining = next_remaining
            feedback = new_feedback
            if remaining and attempt < max_attempts:
                time.sleep(0.5)

        chunk_failed = {src["sku"]: feedback.get(src["sku"], ["Unknown validation failure"]) for src, _, _ in remaining}
        return chunk_idx, chunk_ok, chunk_failed

    completed_chunks = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {
            pool.submit(_process_chunk, idx + 1, ch): idx + 1
            for idx, ch in enumerate(chunks)
        }
        for fut in as_completed(futs):
            _idx, chunk_ok, chunk_failed = fut.result()
            with lock:
                completed_chunks += 1
                results.update(chunk_ok)
                if failed_out is not None and chunk_failed:
                    failed_out.update(chunk_failed)
                print(
                    f"  [gen chunk {completed_chunks}/{total_chunks}] "
                    f"valid_so_far={len(results)} failed_in_chunk={len(chunk_failed)}"
                )

    return results


def select_diverse_sample(
    active_records: list[dict[str, Any]],
    root_dir: Path,
    n: int = 25,
) -> list[dict[str, Any]]:
    """Select a balanced, deterministic N-item sample across all EPSCAN_* folders."""
    actives = [r for r in active_records if r.get("classification") == "ACTIVE"]
    by_dir: dict[str, list[dict[str, Any]]] = {d: [] for d in TARGET_EPSCAN_DIRS}
    for r in actives:
        if r.get("epscan_dir") in by_dir:
            by_dir[r["epscan_dir"]].append(r)

    def _priority_key(r: dict[str, Any]) -> tuple[int, str]:
        meta_p = Path(r["folder_path"]) / "metadata.json"
        score = 0
        try:
            m = json.loads(meta_p.read_text(encoding="utf-8"))
            y_int = extract_year_int(m.get("year"))
            title = r.get("live_title") or m.get("title") or ""
            if len(title) < 62:
                score -= 3
            if y_int is not None and y_int <= ANTIQUE_CUTOFF_YEAR:
                score -= 4
            loc = (m.get("location") or "").strip().lower()
            if loc and loc not in ("united states", "usa", "us", "unknown"):
                score -= 2
            if len((m.get("back_text_transcription") or "").strip()) >= 12:
                score -= 2
            if (m.get("image_color") or "").lower() == "sepia":
                score -= 1
        except Exception:
            pass
        return (score, r["sku"])

    for d in by_dir:
        by_dir[d].sort(key=_priority_key)

    selected: list[dict[str, Any]] = []
    seen_skus: set[str] = set()
    idx = 0
    while len(selected) < n:
        progressed = False
        for d in TARGET_EPSCAN_DIRS:
            bucket = by_dir[d]
            if idx < len(bucket):
                rec = bucket[idx]
                if rec["sku"] not in seen_skus:
                    selected.append(rec)
                    seen_skus.add(rec["sku"])
                    progressed = True
                    if len(selected) >= n:
                        break
        if not progressed:
            break
        idx += 1
    return selected


def run_title_optimizer(
    root_dir: Path,
    target_dir: Optional[Path] = None,
    sku_filter: Optional[str] = None,
    sample: Optional[int] = None,
    count: Optional[int] = None,
    push_live: bool = False,
    store: str = "photo_vault",
    env: str = "production",
    workers: int = 5,
    delay: float = 0.15,
) -> dict[str, Any]:
    records = _load_audit_records(root_dir, target_dir)
    if sku_filter:
        records = [r for r in records if r.get("sku") == sku_filter]

    active_records = [r for r in records if r.get("classification") == "ACTIVE"]
    skipped_non_active = len(records) - len(active_records)

    if sample is not None and sample > 0:
        active_records = select_diverse_sample(active_records, root_dir, n=sample)
    elif count is not None and count > 0:
        active_records = active_records[:count]

    items_with_meta: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for rec in active_records:
        meta_p = Path(rec["folder_path"]) / "metadata.json"
        if meta_p.exists():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            items_with_meta.append((rec, meta))

    cache_dir = root_dir / ".cache" / "ebay_seo_titles"
    print("=" * 78)
    print(
        f"PHASE 2 AI TITLE OPTIMIZER ({store.upper()} | {env.upper()} | "
        f"{'LIVE PUSH' if push_live else 'DRY-RUN'})"
    )
    print("=" * 78)
    print(f"Target ACTIVE items  : {len(items_with_meta)}")
    print(f"Non-ACTIVE skipped   : {skipped_non_active}")
    print(f"Cache directory      : {cache_dir}")

    failed_details: dict[str, list[str]] = {}
    generated_map = generate_and_cache_titles(
        items_with_meta, cache_dir=cache_dir, workers=workers, failed_out=failed_details
    )
    valid_count = len(generated_map)
    print(f"Validated AI titles  : {valid_count}/{len(items_with_meta)} (failed={len(failed_details)})")

    old_lens = []
    new_lens = []
    comparisons: list[dict[str, Any]] = []
    by_dir_valid: Counter[str] = Counter()
    for rec, meta in items_with_meta:
        sku = rec["sku"]
        old_t = rec.get("live_title") or meta.get("title") or ""
        new_t = generated_map.get(sku)
        if new_t:
            old_lens.append(len(old_t))
            new_lens.append(len(new_t))
            by_dir_valid[rec["epscan_dir"]] += 1
            comparisons.append(
                {
                    "epscan_dir": rec["epscan_dir"],
                    "sku": sku,
                    "old_title": old_t,
                    "old_len": len(old_t),
                    "new_title": new_t,
                    "new_len": len(new_t),
                }
            )

    if old_lens:
        print(
            f"Avg title length     : {sum(old_lens)/len(old_lens):.1f} -> {sum(new_lens)/len(new_lens):.1f} chars "
            f"(min={min(new_lens)}, max={max(new_lens)})"
        )
        print(f"By folder validated  : {dict(by_dir_valid)}")

    inv_ok = 0
    inv_fail = 0
    guard_skip = 0
    already_synced = 0

    if push_live and comparisons:
        token_mgr = TokenManager(store=store, env=env)
        token_mgr.get_token()
        api_host = ENVIRONMENTS[env]["api_host"]

        eligible = [(rec, meta) for rec, meta in items_with_meta if rec["sku"] in generated_map]
        total_push_chunks = (len(eligible) + 24) // 25
        for i in range(0, len(eligible), 25):
            chunk = eligible[i : i + 25]
            chunk_num = i // 25 + 1
            skus = [r["sku"] for r, _ in chunk]
            bulk_inv = fetch_bulk_inventory(skus, token_mgr, api_host)
            chunk_offers: dict[str, tuple[int, dict[str, Any]]] = {}
            with ThreadPoolExecutor(max_workers=5) as pool:
                futs = {
                    pool.submit(fetch_single_offer, r, token_mgr, api_host): r["sku"]
                    for r, _ in chunk
                }
                for fut in as_completed(futs):
                    s, c, body = fut.result()
                    chunk_offers[s] = (c, body)

            batch_push: list[tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any], Path]] = []
            for rec, meta in chunk:
                sku = rec["sku"]
                folder = Path(rec["folder_path"])
                meta_p = folder / "metadata.json"
                live_inv = bulk_inv.get(sku, {})
                off_code, live_offer = chunk_offers.get(sku, (0, {}))
                live_cls = classify_live_item(live_inv, off_code, live_offer)
                if live_cls != "ACTIVE":
                    guard_skip += 1
                    print(f"  [GUARD SKIP] {sku} live status={live_cls}; skipping.")
                    if live_cls == "SOLD" and meta_p.exists():
                        meta["ebay_sold"] = True
                        meta["ebay_listing_status"] = "SOLD"
                        meta_p.write_text(json.dumps(meta, indent=4), encoding="utf-8")
                    continue
                inv_item = (
                    live_inv["inventoryItem"]
                    if isinstance(live_inv.get("inventoryItem"), dict)
                    else live_inv
                )
                prod = dict(inv_item.get("product") or {})
                new_title = generated_map[sku]
                if prod.get("title") == new_title:
                    already_synced += 1
                    continue
                prod["title"] = new_title
                aspects = prod.get("aspects") or {}
                prod["description"] = build_html_description({**meta, "title": new_title}, aspects)

                payload: dict[str, Any] = {
                    "availability": inv_item.get("availability")
                    or {"shipToLocationAvailability": {"quantity": 1}},
                    "condition": inv_item.get("condition") or "USED_EXCELLENT",
                    "product": prod,
                }
                if inv_item.get("conditionDescription"):
                    payload["conditionDescription"] = inv_item["conditionDescription"]
                if inv_item.get("packageWeightAndSize"):
                    payload["packageWeightAndSize"] = inv_item["packageWeightAndSize"]
                batch_push.append((rec, payload, folder, meta, meta_p))

            if batch_push:
                if len(batch_push) == 1:
                    r, p, fdir, m, mp = batch_push[0]
                    ok, code, msg = _push_single_inventory(r["sku"], p, token_mgr, api_host)
                    outcomes = {r["sku"]: (ok, code, msg)}
                else:
                    outcomes = _push_bulk_inventory(
                        [(r, p) for r, p, _, _, _ in batch_push], token_mgr, api_host
                    )
                for r, p, fdir, m, mp in batch_push:
                    sku = r["sku"]
                    ok, code, msg = outcomes.get(sku, (False, 0, "missing"))
                    if not ok and (code == 0 or code == 429 or code >= 500):
                        time.sleep(1.5)
                        ok, code, msg = _push_single_inventory(sku, p, token_mgr, api_host)
                    if ok:
                        inv_ok += 1
                        (fdir / "inventory_item.json").write_text(
                            json.dumps(p, indent=4), encoding="utf-8"
                        )
                        m["title"] = p["product"]["title"]
                        mp.write_text(json.dumps(m, indent=4), encoding="utf-8")
                    else:
                        inv_fail += 1
                        print(f"  [INV FAIL] {sku} HTTP {code}: {msg}")

            print(
                f"  [push chunk {chunk_num}/{total_push_chunks}] "
                f"ok={inv_ok} skipped_same={already_synced} fail={inv_fail} guard_skip={guard_skip}"
            )
            if delay > 0:
                time.sleep(delay)

        print(
            f"Live push OK / AlreadySynced / Fail / Guard : "
            f"{inv_ok} / {already_synced} / {inv_fail} / {guard_skip}"
        )

    if not push_live:
        report_path = root_dir / ".cache" / "phase2_title_dry_run_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_payload = {
            "evaluated": len(items_with_meta),
            "non_active_skipped": skipped_non_active,
            "validated": valid_count,
            "failed_count": len(failed_details),
            "by_folder": dict(by_dir_valid),
            "avg_old_len": round(sum(old_lens) / len(old_lens), 2) if old_lens else 0,
            "avg_new_len": round(sum(new_lens) / len(new_lens), 2) if new_lens else 0,
            "failed_skus": failed_details,
            "comparisons": comparisons,
        }
        report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
        print(f"[+] Saved dry-run report to {report_path}")

    if not push_live and comparisons:
        if len(comparisons) <= 30:
            preview_list = comparisons
            header_label = f"BEFORE / AFTER PREVIEW ({len(comparisons)} ITEMS)"
        else:
            step = max(1, len(comparisons) // 10)
            preview_list = [comparisons[i] for i in range(0, len(comparisons), step)][:10]
            header_label = f"BEFORE / AFTER SAMPLE PREVIEW (10 OF {len(comparisons)} ITEMS)"
        print("\n" + "=" * 78)
        print(header_label)
        print("=" * 78)
        for c in preview_list:
            print(
                f"{c['epscan_dir']} | {c['sku']} | ({c['old_len']}->{c['new_len']})\n"
                f"  OLD: {c['old_title']}\n"
                f"  NEW: {c['new_title']}"
            )

    return {
        "evaluated": len(items_with_meta),
        "valid": valid_count,
        "failed": len(failed_details),
        "comparisons": comparisons,
        "inv_ok": inv_ok,
        "inv_fail": inv_fail,
        "guard_skip": guard_skip,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 2 Gemini AI Title Optimizer (dry-run by default)."
    )
    parser.add_argument("--dir", default=None)
    parser.add_argument("--sku", default=None)
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--push-live",
        action="store_true",
        help="Explicit opt-in required to push generated titles to live eBay and update local JSONs.",
    )
    parser.add_argument("--store", default="photo_vault")
    parser.add_argument("--env", choices=["sandbox", "production"], default="production")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()

    push_live = bool(args.push_live and not args.dry_run)
    root_dir = Path(__file__).parent.resolve()
    target_dir = Path(args.dir).resolve() if args.dir else None
    run_title_optimizer(
        root_dir=root_dir,
        target_dir=target_dir,
        sku_filter=args.sku,
        sample=args.sample,
        count=args.count,
        push_live=push_live,
        store=args.store,
        env=args.env,
        workers=args.workers,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()

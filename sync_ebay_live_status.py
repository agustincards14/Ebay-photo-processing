"""Read-only live eBay audit & Category 262421 Taxonomy fetcher (Phase 0).

Fetches:
1. Official eBay Category 262421 (Photographs) item aspects from Commerce Taxonomy API
   -> saved to `.cache/ebay_taxonomy_262421.json`
2. Live inventory item + offer status for all published items across EPSCAN_* folders
   (excluding EPSCAN_TEST and cheap_photos)
   -> saved to `.cache/ebay_live_audit.json`

Strictly read-only against eBay. Never modifies local metadata.json unless
`--tag-metadata` is explicitly passed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from ebay_oauth_helper import ENVIRONMENTS, check_and_get_token, load_credentials

TARGET_EPSCAN_DIRS = ["EPSCAN_7_26", "EPSCAN_7_30", "EPSCAN_8_23", "EPSCAN_9_20"]
CATEGORY_ID = "262421"
KEY_ASPECT_NAMES = {
    "Time Period Manufactured",
    "Format",
    "Type",
    "Production Technique",
    "Antique",
    "Vintage",
    "Subject",
    "Theme",
    "Region of Origin",
    "Size Type/Largest Dimension",
    "Features",
    "Style",
    "Image Color",
    "Image Orientation",
    "Original/Licensed Reprint",
    "Framing",
    "Year of Production",
    "Decade",
}


class TokenManager:
    """Thread-safe eBay User OAuth token manager."""

    def __init__(self, store: str = "photo_vault", env: str = "production") -> None:
        self.store = store
        self.env = env
        self._lock = threading.Lock()
        self._token: Optional[str] = None

    def get_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            if force_refresh or not self._token:
                token = check_and_get_token(store=self.store, env=self.env)
                if not token:
                    raise RuntimeError(
                        f"Could not obtain valid access token for {self.store} ({self.env})."
                    )
                self._token = token
            return self._token


def get_application_token(
    store: str = "photo_vault", env: str = "production"
) -> str:
    """Mint an eBay Application OAuth token using client_credentials."""
    creds_all = load_credentials()
    creds = creds_all.get(store, {}).get(env, {})
    client_id = creds.get("client_id", "")
    client_secret = creds.get("client_secret", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            f"Missing client_id or client_secret for {store}.{env} in ebay_credentials.json"
        )

    token_url = ENVIRONMENTS[env]["token_url"]
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode(
        "utf-8"
    )
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {basic}",
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }
    resp = requests.post(token_url, headers=headers, data=data, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to mint application token: HTTP {resp.status_code} - {resp.text[:300]}"
        )
    return resp.json()["access_token"]


def fetch_taxonomy_262421(
    cache_path: Path, store: str = "photo_vault", env: str = "production"
) -> dict[str, Any]:
    """Fetch Category 262421 item aspects from eBay Commerce Taxonomy API."""
    app_token = get_application_token(store=store, env=env)
    api_host = ENVIRONMENTS[env]["api_host"]
    url = (
        f"{api_host}/commerce/taxonomy/v1/category_tree/0/"
        f"get_item_aspects_for_category?category_id={CATEGORY_ID}"
    )
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Taxonomy API failed: HTTP {resp.status_code} - {resp.text[:400]}"
        )
    data = resp.json()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def print_taxonomy_summary(taxonomy_data: dict[str, Any]) -> None:
    """Print concise summary of Category 262421 aspects."""
    aspects = taxonomy_data.get("aspects", [])
    print("\n" + "=" * 78)
    print(f"CATEGORY {CATEGORY_ID} TAXONOMY ASPECTS SUMMARY (total={len(aspects)})")
    print("=" * 78)
    for asp in aspects:
        name = asp.get("localizedAspectName", "")
        cons = asp.get("aspectConstraint", {})
        req = cons.get("aspectRequired", False)
        usage = cons.get("aspectUsage", "")
        mode = cons.get("aspectMode", "")
        card = cons.get("itemToAspectCardinality", "")
        vals = [v.get("localizedValue", "") for v in asp.get("aspectValues", [])]
        print(
            f"- {name}: required={req}, usage={usage}, mode={mode}, "
            f"cardinality={card}, values_count={len(vals)}"
        )
        if name in KEY_ASPECT_NAMES and vals:
            if len(vals) <= 30:
                print(f"    values: {vals}")
            else:
                print(f"    sample_values (first 20): {vals[:20]}")


def discover_published_items(root_dir: Path) -> list[dict[str, Any]]:
    """Discover all published items in EPSCAN_7_26..EPSCAN_9_20."""
    items: list[dict[str, Any]] = []
    for dir_name in TARGET_EPSCAN_DIRS:
        epscan_dir = root_dir / dir_name
        if not epscan_dir.exists():
            continue
        for meta_path in sorted(epscan_dir.rglob("metadata.json")):
            if "cheap_photos" in meta_path.parts or "EPSCAN_TEST" in meta_path.parts:
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not meta.get("ebay_offer_published"):
                continue
            rel_path = str(meta_path.parent.relative_to(root_dir))
            items.append(
                {
                    "epscan_dir": dir_name,
                    "rel_path": rel_path,
                    "folder_path": str(meta_path.parent),
                    "sku": meta.get("sku") or meta_path.parent.name,
                    "offer_id": meta.get("ebay_offer_id"),
                    "listing_url": meta.get("ebay_listing_url"),
                }
            )
    return items


def _request_with_retry(
    method: str,
    url: str,
    token_mgr: TokenManager,
    json_body: Optional[dict[str, Any]] = None,
    max_retries: int = 4,
) -> tuple[int, Any]:
    """Execute HTTP request with 401 refresh and 429/5xx exponential backoff."""
    for attempt in range(max_retries + 1):
        token = token_mgr.get_token(force_refresh=(attempt > 0 and attempt == 1))
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Language": "en-US",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            resp = requests.request(
                method, url, headers=headers, json=json_body, timeout=25
            )
            if resp.status_code == 401 and attempt < max_retries:
                token_mgr.get_token(force_refresh=True)
                time.sleep(0.5)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < max_retries:
                    retry_after = resp.headers.get("Retry-After")
                    wait_s = (
                        float(retry_after)
                        if retry_after and retry_after.isdigit()
                        else min(16.0, 1.0 * (2**attempt))
                    )
                    time.sleep(wait_s)
                    continue
            try:
                body = resp.json() if resp.text else {}
            except Exception:
                body = {"raw_text": resp.text[:500]}
            return resp.status_code, body
        except requests.RequestException as exc:
            if attempt < max_retries:
                time.sleep(min(10.0, 1.0 * (2**attempt)))
                continue
            return 0, {"error": str(exc)}
    return 0, {"error": "max_retries_exceeded"}


def fetch_bulk_inventory(
    skus: list[str], token_mgr: TokenManager, api_host: str
) -> dict[str, dict[str, Any]]:
    """Fetch live inventory items in chunks of 25 via bulk_get_inventory_item."""
    url = f"{api_host}/sell/inventory/v1/bulk_get_inventory_item"
    results: dict[str, dict[str, Any]] = {}
    total_chunks = (len(skus) + 24) // 25
    for idx in range(0, len(skus), 25):
        chunk = skus[idx : idx + 25]
        chunk_num = idx // 25 + 1
        payload = {"requests": [{"sku": s} for s in chunk]}
        status, body = _request_with_retry("POST", url, token_mgr, json_body=payload)
        if status in (200, 207) and isinstance(body, dict):
            for rec in body.get("responses", []):
                r_sku = rec.get("sku")
                if r_sku:
                    results[r_sku] = rec
        else:
            for s in chunk:
                results[s] = {"sku": s, "statusCode": status, "error": body}
        if chunk_num % 20 == 0 or chunk_num == total_chunks:
            print(
                f"  [bulk_get_inventory_item] {chunk_num}/{total_chunks} chunks ({min(idx + 25, len(skus))}/{len(skus)} SKUs)"
            )
        time.sleep(0.05)
    return results


def fetch_single_offer(
    item: dict[str, Any], token_mgr: TokenManager, api_host: str
) -> tuple[str, int, dict[str, Any]]:
    """Fetch live offer by offer_id, falling back to GET /offer?sku={sku}."""
    sku = item["sku"]
    offer_id = item.get("offer_id")
    if offer_id:
        url = f"{api_host}/sell/inventory/v1/offer/{offer_id}"
        status, body = _request_with_retry("GET", url, token_mgr)
        if status == 200 and isinstance(body, dict) and body.get("offerId"):
            return sku, 200, body
        if status not in (404, 400):
            return sku, status, body if isinstance(body, dict) else {}

    # Fallback by SKU
    url = f"{api_host}/sell/inventory/v1/offer?sku={requests.utils.quote(sku, safe='')}"
    status, body = _request_with_retry("GET", url, token_mgr)
    if status == 200 and isinstance(body, dict):
        offers = body.get("offers", [])
        if offers:
            return sku, 200, offers[0]
        return sku, 404, {"error": "no_offers_for_sku"}
    return sku, status, body if isinstance(body, dict) else {}


def _unwrap_inv(inv_resp: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(inv_resp, dict):
        return {}
    if "inventoryItem" in inv_resp and isinstance(inv_resp["inventoryItem"], dict):
        return inv_resp["inventoryItem"]
    return inv_resp


def classify_live_item(
    inv_resp: Optional[dict[str, Any]], offer_status_code: int, offer_resp: Optional[dict[str, Any]]
) -> str:
    """Strictly classify live listing state to prevent modifying sold/ended items."""
    if not offer_resp or offer_status_code in (404, 0) or offer_status_code >= 400:
        if offer_status_code == 404:
            return "NOT_FOUND"
        return "ERROR"

    if not inv_resp or inv_resp.get("statusCode", 200) >= 400:
        if inv_resp and inv_resp.get("statusCode") == 404:
            return "NOT_FOUND"
        return "ERROR"

    inv_item = _unwrap_inv(inv_resp)
    inv_qty = (
        inv_item.get("availability", {})
        .get("shipToLocationAvailability", {})
        .get("quantity")
    )
    if inv_qty is None:
        inv_qty = 0

    offer_status = offer_resp.get("status")
    listing = offer_resp.get("listing") or {}
    listing_status = listing.get("listingStatus")
    sold_qty = listing.get("soldQuantity") or 0
    avail_qty = offer_resp.get("availableQuantity")

    if sold_qty > 0:
        return "SOLD"
    if (
        offer_status == "PUBLISHED"
        and listing_status == "ACTIVE"
        and sold_qty == 0
        and inv_qty >= 1
        and (avail_qty is None or avail_qty >= 1)
    ):
        return "ACTIVE"
    if listing_status == "ENDED" and sold_qty == 0:
        return "ENDED_UNSOLD"
    if (listing_status == "OUT_OF_STOCK" or inv_qty == 0) and sold_qty == 0:
        return "OUT_OF_STOCK_UNSOLD"
    if offer_status == "UNPUBLISHED":
        return "UNPUBLISHED"
    return "ERROR"


def run_audit(
    root_dir: Path,
    store: str = "photo_vault",
    env: str = "production",
    tag_metadata: bool = False,
) -> dict[str, Any]:
    cache_dir = root_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tax_path = cache_dir / "ebay_taxonomy_262421.json"
    audit_path = cache_dir / "ebay_live_audit.json"

    print(f"[*] Fetching Category {CATEGORY_ID} Taxonomy aspects...")
    taxonomy_data = fetch_taxonomy_262421(tax_path, store=store, env=env)
    print(f"[+] Saved Taxonomy to {tax_path}")
    print_taxonomy_summary(taxonomy_data)

    items = discover_published_items(root_dir)
    print(f"\n[*] Discovered {len(items)} published items across {TARGET_EPSCAN_DIRS}.")

    token_mgr = TokenManager(store=store, env=env)
    token_mgr.get_token()
    api_host = ENVIRONMENTS[env]["api_host"]

    skus = [it["sku"] for it in items]
    print("[*] Fetching live inventory items in batches of 25...")
    inv_map = fetch_bulk_inventory(skus, token_mgr, api_host)

    print("[*] Fetching live offers (5 concurrent workers)...")
    offer_map: dict[str, tuple[int, dict[str, Any]]] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(fetch_single_offer, it, token_mgr, api_host): it["sku"]
            for it in items
        }
        for fut in as_completed(futures):
            sku, code, body = fut.result()
            offer_map[sku] = (code, body)
            completed += 1
            if completed % 200 == 0 or completed == len(items):
                print(f"  [get_offer] {completed}/{len(items)} completed")

    audit_records: list[dict[str, Any]] = []
    by_dir_counts: dict[str, Counter[str]] = defaultdict(Counter)
    active_not_2499: dict[str, int] = defaultdict(int)
    active_best_offer_on: dict[str, int] = defaultdict(int)
    v17_record: Optional[dict[str, Any]] = None

    for it in items:
        sku = it["sku"]
        inv_resp = inv_map.get(sku, {})
        offer_code, offer_resp = offer_map.get(sku, (0, {}))
        classification = classify_live_item(inv_resp, offer_code, offer_resp)

        inv_item = _unwrap_inv(inv_resp)
        inv_qty = (
            inv_item.get("availability", {})
            .get("shipToLocationAvailability", {})
            .get("quantity")
        )
        prod = inv_item.get("product", {}) if isinstance(inv_item, dict) else {}
        image_urls = prod.get("imageUrls", [])
        listing = offer_resp.get("listing") or {}
        price_val = (
            offer_resp.get("pricingSummary", {}).get("price", {}).get("value")
        )
        best_offer_on = bool(
            offer_resp.get("listingPolicies", {})
            .get("bestOfferTerms", {})
            .get("bestOfferEnabled")
        )

        rec = {
            "epscan_dir": it["epscan_dir"],
            "rel_path": it["rel_path"],
            "folder_path": it["folder_path"],
            "sku": sku,
            "offer_id": offer_resp.get("offerId") or it.get("offer_id"),
            "listing_id": listing.get("listingId"),
            "classification": classification,
            "offer_http_status": offer_code,
            "inv_http_status": inv_resp.get("statusCode", 200 if prod else 0),
            "offer_status": offer_resp.get("status"),
            "listing_status": listing.get("listingStatus"),
            "sold_quantity": listing.get("soldQuantity", 0),
            "live_inv_qty": inv_qty,
            "available_quantity": offer_resp.get("availableQuantity"),
            "live_price": price_val,
            "best_offer_enabled": best_offer_on,
            "live_image_count": len(image_urls),
            "live_title": prod.get("title"),
            "live_inventory": inv_resp if inv_resp.get("statusCode", 200) < 400 else None,
            "live_offer": offer_resp if offer_code == 200 else None,
        }
        audit_records.append(rec)

        by_dir_counts[it["epscan_dir"]][classification] += 1
        by_dir_counts[it["epscan_dir"]]["TOTAL"] += 1
        if classification == "ACTIVE":
            if price_val != "24.99":
                active_not_2499[it["epscan_dir"]] += 1
            if best_offer_on:
                active_best_offer_on[it["epscan_dir"]] += 1

        if it["rel_path"] == "EPSCAN_7_26/AIPH-1-V/AIPH-1-V17":
            v17_record = {k: v for k, v in rec.items() if k not in ("live_inventory", "live_offer")}

        if tag_metadata and classification != "ERROR":
            meta_p = Path(it["folder_path"]) / "metadata.json"
            if meta_p.exists():
                try:
                    m = json.loads(meta_p.read_text(encoding="utf-8"))
                    changed = False
                    if classification == "SOLD" and not m.get("ebay_sold"):
                        m["ebay_sold"] = True
                        changed = True
                    if m.get("ebay_listing_status") != classification:
                        m["ebay_listing_status"] = classification
                        changed = True
                    if changed:
                        meta_p.write_text(json.dumps(m, indent=4), encoding="utf-8")
                except Exception:
                    pass

    audit_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "store": store,
        "env": env,
        "total_published": len(audit_records),
        "records": audit_records,
    }
    audit_path.write_text(json.dumps(audit_payload, indent=2), encoding="utf-8")
    print(f"\n[+] Saved live audit to {audit_path}")

    print("\n" + "=" * 96)
    print("LIVE EBAY AUDIT SUMMARY BY FOLDER")
    print("=" * 96)
    header = (
        f"{'Folder':<13} | {'Total':>5} | {'ACTIVE':>6} | {'SOLD':>5} | "
        f"{'ENDED':>5} | {'OOS':>4} | {'UNPUB':>5} | {'404/ERR':>7} | "
        f"{'ACT!=24.99':>10} | {'ACT BestOff':>11}"
    )
    print(header)
    print("-" * 96)
    totals: Counter[str] = Counter()
    tot_not2499 = 0
    tot_bo = 0
    for d in TARGET_EPSCAN_DIRS:
        c = by_dir_counts[d]
        err_cnt = c["NOT_FOUND"] + c["ERROR"]
        n24 = active_not_2499[d]
        bo = active_best_offer_on[d]
        tot_not2499 += n24
        tot_bo += bo
        for k, v in c.items():
            totals[k] += v
        print(
            f"{d:<13} | {c['TOTAL']:>5} | {c['ACTIVE']:>6} | {c['SOLD']:>5} | "
            f"{c['ENDED_UNSOLD']:>5} | {c['OUT_OF_STOCK_UNSOLD']:>4} | "
            f"{c['UNPUBLISHED']:>5} | {err_cnt:>7} | {n24:>10} | {bo:>11}"
        )
    print("-" * 96)
    tot_err = totals["NOT_FOUND"] + totals["ERROR"]
    print(
        f"{'TOTAL':<13} | {totals['TOTAL']:>5} | {totals['ACTIVE']:>6} | {totals['SOLD']:>5} | "
        f"{totals['ENDED_UNSOLD']:>5} | {totals['OUT_OF_STOCK_UNSOLD']:>4} | "
        f"{totals['UNPUBLISHED']:>5} | {tot_err:>7} | {tot_not2499:>10} | {tot_bo:>11}"
    )

    if v17_record:
        print("\n[AIPH-1-V17 Live Status]:", json.dumps(v17_record, indent=2))

    return audit_payload


def recompute_audit_cache(root_dir: Path) -> dict[str, Any]:
    """Re-classify existing .cache/ebay_live_audit.json without hitting eBay API."""
    audit_path = root_dir / ".cache" / "ebay_live_audit.json"
    data = json.loads(audit_path.read_text(encoding="utf-8"))
    by_dir_counts: dict[str, Counter[str]] = defaultdict(Counter)
    active_not_2499: dict[str, int] = defaultdict(int)
    active_best_offer_on: dict[str, int] = defaultdict(int)
    v17_record: Optional[dict[str, Any]] = None

    for rec in data.get("records", []):
        inv_resp = rec.get("live_inventory") or {"statusCode": rec.get("inv_http_status", 404)}
        offer_resp = rec.get("live_offer") or {}
        offer_code = rec.get("offer_http_status", 404)
        cls = classify_live_item(inv_resp, offer_code, offer_resp)

        inv_item = _unwrap_inv(inv_resp)
        inv_qty = (
            inv_item.get("availability", {})
            .get("shipToLocationAvailability", {})
            .get("quantity")
        )
        prod = inv_item.get("product", {}) if isinstance(inv_item, dict) else {}
        rec["classification"] = cls
        rec["live_inv_qty"] = inv_qty
        rec["live_image_count"] = len(prod.get("imageUrls", []))
        rec["live_title"] = prod.get("title")

        d = rec["epscan_dir"]
        by_dir_counts[d][cls] += 1
        by_dir_counts[d]["TOTAL"] += 1
        if cls == "ACTIVE":
            if rec.get("live_price") != "24.99":
                active_not_2499[d] += 1
            if rec.get("best_offer_enabled"):
                active_best_offer_on[d] += 1
        if rec.get("rel_path") == "EPSCAN_7_26/AIPH-1-V/AIPH-1-V17":
            v17_record = {k: v for k, v in rec.items() if k not in ("live_inventory", "live_offer")}

    audit_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[+] Recomputed audit cache at {audit_path}")
    print("\n" + "=" * 96)
    print("LIVE EBAY AUDIT SUMMARY BY FOLDER")
    print("=" * 96)
    header = (
        f"{'Folder':<13} | {'Total':>5} | {'ACTIVE':>6} | {'SOLD':>5} | "
        f"{'ENDED':>5} | {'OOS':>4} | {'UNPUB':>5} | {'404/ERR':>7} | "
        f"{'ACT!=24.99':>10} | {'ACT BestOff':>11}"
    )
    print(header)
    print("-" * 96)
    totals: Counter[str] = Counter()
    tot_not2499 = 0
    tot_bo = 0
    for d in TARGET_EPSCAN_DIRS:
        c = by_dir_counts[d]
        err_cnt = c["NOT_FOUND"] + c["ERROR"]
        n24 = active_not_2499[d]
        bo = active_best_offer_on[d]
        tot_not2499 += n24
        tot_bo += bo
        for k, v in c.items():
            totals[k] += v
        print(
            f"{d:<13} | {c['TOTAL']:>5} | {c['ACTIVE']:>6} | {c['SOLD']:>5} | "
            f"{c['ENDED_UNSOLD']:>5} | {c['OUT_OF_STOCK_UNSOLD']:>4} | "
            f"{c['UNPUBLISHED']:>5} | {err_cnt:>7} | {n24:>10} | {bo:>11}"
        )
    print("-" * 96)
    tot_err = totals["NOT_FOUND"] + totals["ERROR"]
    print(
        f"{'TOTAL':<13} | {totals['TOTAL']:>5} | {totals['ACTIVE']:>6} | {totals['SOLD']:>5} | "
        f"{totals['ENDED_UNSOLD']:>5} | {totals['OUT_OF_STOCK_UNSOLD']:>4} | "
        f"{totals['UNPUBLISHED']:>5} | {tot_err:>7} | {tot_not2499:>10} | {tot_bo:>11}"
    )
    if v17_record:
        print("\n[AIPH-1-V17 Live Status]:", json.dumps(v17_record, indent=2))
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only Phase 0 eBay Taxonomy & Live Status Audit."
    )
    parser.add_argument("--store", default="photo_vault", help="eBay store key")
    parser.add_argument(
        "--env",
        choices=["sandbox", "production"],
        default="production",
        help="eBay environment",
    )
    parser.add_argument(
        "--tag-metadata",
        action="store_true",
        help="Persist ebay_sold / ebay_listing_status in local metadata.json (default: OFF)",
    )
    parser.add_argument(
        "--recompute-cache",
        action="store_true",
        help="Re-classify existing .cache/ebay_live_audit.json without calling eBay API",
    )
    args = parser.parse_args()
    root_dir = Path(__file__).parent.resolve()
    if args.recompute_cache:
        recompute_audit_cache(root_dir)
    else:
        run_audit(root_dir, store=args.store, env=args.env, tag_metadata=args.tag_metadata)


if __name__ == "__main__":
    main()

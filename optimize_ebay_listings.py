"""Phase 1 Deterministic eBay SEO, $24.99 Pricing, and Best Offer Removal Updater.

Sold-Safe Guarantees:
- Never mutates any item unless live eBay status is strictly ACTIVE
  (status == PUBLISHED, listingStatus == ACTIVE, soldQuantity == 0,
   live inventory quantity >= 1, offer availableQuantity >= 1).
- In live write mode, always re-fetches live inventory & offer immediately
  before writing and merges updates onto the live payload.
- Never calls publishOffer on ended/sold items.
- Never modifies local JSON files during --dry-run.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from ebay_oauth_helper import ENVIRONMENTS
from sync_ebay_live_status import (
    TARGET_EPSCAN_DIRS,
    TokenManager,
    _request_with_retry,
    classify_live_item,
    fetch_bulk_inventory,
    fetch_single_offer,
)

ALLOWED_PUT_OFFER_KEYS = {
    "availableQuantity",
    "categoryId",
    "charity",
    "extendedProducerResponsibility",
    "hideBuyerDetails",
    "includeCatalogProductDetails",
    "listingDescription",
    "listingDuration",
    "listingPolicies",
    "listingStartDate",
    "lotSize",
    "merchantLocationKey",
    "pricingSummary",
    "quantityLimitPerBuyer",
    "regulatory",
    "secondaryCategoryId",
    "storeCategoryNames",
    "tax",
}

TARGET_PRICE = "24.99"
ANTIQUE_CUTOFF_YEAR = 1926
VINTAGE_CUTOFF_YEAR = 2006


def load_taxonomy_constraints(taxonomy_path: Path) -> dict[str, dict[str, Any]]:
    """Load Category 262421 aspect constraints and allowed values."""
    if not taxonomy_path.exists():
        return {}
    data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for asp in data.get("aspects", []):
        name = asp.get("localizedAspectName")
        if not name:
            continue
        cons = asp.get("aspectConstraint", {})
        vals = [v.get("localizedValue", "") for v in asp.get("aspectValues", []) if v.get("localizedValue")]
        out[name] = {
            "required": bool(cons.get("aspectRequired", False)),
            "usage": cons.get("aspectUsage", "OPTIONAL"),
            "mode": cons.get("aspectMode", "FREE_TEXT"),
            "cardinality": cons.get("itemToAspectCardinality", "SINGLE"),
            "values": vals,
            "values_set": set(vals),
        }
    return out


def extract_year_int(year_raw: Any) -> Optional[int]:
    """Extract a 4-digit year integer from strings like '1958', 'c. 1950', '1960s'."""
    if year_raw is None:
        return None
    s = str(year_raw).strip()
    if not s or "unknown" in s.lower():
        return None
    m = re.search(r"\b(18\d{2}|19\d{2}|20\d{2})\b", s)
    if m:
        return int(m.group(1))
    m_dec = re.search(r"\b(18\d|19\d|20\d)0s\b", s.lower())
    if m_dec:
        return int(m_dec.group(1) + "0")
    return None


def map_time_period_manufactured(year_int: Optional[int]) -> Optional[str]:
    """Map a 4-digit year to eBay Category 262421 Time Period Manufactured bucket."""
    if year_int is None:
        return None
    if year_int < 1850:
        return "Pre-1850"
    if year_int <= 1899:
        return "1850-1899"
    if year_int <= 1924:
        return "1900-1924"
    if year_int <= 1949:
        return "1925-1949"
    if year_int <= 1959:
        return "1950-1959"
    if year_int <= 1969:
        return "1960-1969"
    if year_int <= 1979:
        return "1970-1979"
    if year_int <= 1989:
        return "1980-1989"
    if year_int <= 1999:
        return "1990-1999"
    if year_int <= 2009:
        return "2000-2009"
    if year_int <= 2019:
        return "2010-2019"
    return "2020-Now"


def fix_title_photo_keyword(title: str) -> str:
    """Conservatively ensure 'Photo' appears in title while keeping <= 80 chars."""
    clean = re.sub(r"\s+", " ", (title or "").strip())
    if not clean:
        return "Original Vintage Snapshot Photo"
    if "photo" in clean.lower():
        return clean[:80] if len(clean) > 80 else clean

    candidate = f"{clean} Photo"
    if len(candidate) <= 80:
        return candidate

    # Try compacting Black & White -> B&W
    compacted = re.sub(r"\bBlack\s*&\s*White\b", "B&W", clean, flags=re.IGNORECASE)
    if f"{compacted} Photo" != candidate and len(f"{compacted} Photo") <= 80:
        return f"{compacted} Photo"

    # Try removing trailing generic ", USA" or ", US"
    trimmed = re.sub(r",?\s*\b(USA|US)\b\.?$", "", compacted, flags=re.IGNORECASE).strip(" ,")
    if len(f"{trimmed} Photo") <= 80:
        return f"{trimmed} Photo"

    # Word-boundary truncation to fit " Photo" (6 chars)
    max_prefix = 80 - 6
    words = trimmed.split(" ")
    kept: list[str] = []
    for w in words:
        trial = " ".join(kept + [w])
        if len(trial) <= max_prefix:
            kept.append(w)
        else:
            break
    prefix = " ".join(kept).rstrip(" ,;-") if kept else trimmed[:max_prefix].rstrip()
    return f"{prefix} Photo"


def build_html_description(meta: dict[str, Any], aspects: dict[str, list[str]]) -> str:
    """Build clean, mobile-friendly, eBay-compliant HTML description."""
    title = html.escape(str(meta.get("title") or "Vintage Original Photograph").strip())
    desc = html.escape(str(meta.get("description") or "").strip())
    back_text = str(meta.get("back_text_transcription") or "").strip()

    year_val = (aspects.get("Year of Production") or [meta.get("year") or "Unknown"])[0]
    era_val = (aspects.get("Time Period Manufactured") or [""])[0]
    fmt_val = (aspects.get("Format") or [meta.get("photo_type") or "Snapshot"])[0]
    size_val = (aspects.get("Size") or [meta.get("size") or ""])[0]
    orient_val = (aspects.get("Image Orientation") or [""])[0]
    color_val = (aspects.get("Image Color") or [meta.get("image_color") or "Black & White"])[0]
    loc_val = str(meta.get("location") or (aspects.get("Country of Origin") or ["United States"])[0]).strip()

    parts = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#222;max-width:760px;">',
        f'<h2 style="font-size:17px;margin:0 0 10px 0;color:#111;">{title}</h2>',
    ]
    if desc:
        parts.append(f'<p style="margin:0 0 12px 0;">{desc}</p>')

    if back_text:
        parts.append(
            '<div style="background:#f7f7f7;border-left:3px solid #555;padding:8px 12px;margin:0 0 12px 0;">'
            f'<strong>Back of Photo Inscription / Stamp:</strong><br/>&ldquo;{html.escape(back_text)}&rdquo;'
            "</div>"
        )

    specs: list[tuple[str, str]] = [
        ("Authenticity", "Original Vintage Photograph (One of a Kind)"),
        ("Format", fmt_val),
        ("Year / Era", f"{year_val} ({era_val})" if era_val and era_val not in str(year_val) else str(year_val)),
        ("Dimensions", size_val),
        ("Orientation", orient_val),
        ("Color", color_val),
        ("Location", loc_val),
    ]
    parts.append('<h3 style="font-size:14px;margin:0 0 6px 0;color:#111;">Photograph Details</h3>')
    parts.append('<ul style="margin:0 0 12px 18px;padding:0;">')
    for label, val in specs:
        if val:
            parts.append(f"<li><strong>{html.escape(label)}:</strong> {html.escape(str(val))}</li>")
    parts.append("</ul>")

    parts.append(
        '<p style="font-size:13px;color:#444;margin:0;">'
        "<strong>Condition &amp; Archival Shipping:</strong> Original vintage print showing light age/handling wear "
        "consistent with its era; please inspect the high-resolution front and back scans. "
        "Shipped securely inside a protective archival sleeve with rigid cardboard backing."
        "</p>"
    )
    parts.append("</div>")
    return "".join(parts)


def build_condition_description(meta: dict[str, Any], aspects: dict[str, list[str]]) -> str:
    """Build an accurate, trust-building conditionDescription."""
    year_val = (aspects.get("Year of Production") or [meta.get("year") or "Unknown"])[0]
    size_val = (aspects.get("Size") or [meta.get("size") or ""])[0]
    is_antique = (aspects.get("Antique") or ["No"])[0] == "Yes"
    era_word = "antique" if is_antique else "vintage"
    details = ", ".join([p for p in [f"Year: {year_val}" if year_val else "", f"Size: {size_val}" if size_val else ""] if p])
    suffix = f" ({details})" if details else ""
    return (
        f"Original {era_word} photograph{suffix}. Shows typical light age and handling wear "
        "consistent with its era; please review high-resolution front and back scans."
    )


def _validate_selection_only(
    aspect_name: str,
    values: list[str],
    taxonomy: dict[str, dict[str, Any]],
    fallback: Optional[list[str]] = None,
) -> Optional[list[str]]:
    """Ensure SELECTION_ONLY aspects strictly match eBay Taxonomy allowed values."""
    cons = taxonomy.get(aspect_name)
    if not cons:
        return values
    if cons.get("mode") != "SELECTION_ONLY" or not cons.get("values_set"):
        return values
    allowed = cons["values_set"]
    valid = [v for v in values if v in allowed]
    if valid:
        return valid[:1] if cons.get("cardinality") == "SINGLE" else valid
    return fallback


def build_optimized_inventory_payload(
    live_inv: dict[str, Any],
    meta: dict[str, Any],
    taxonomy: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merge deterministic SEO upgrades onto the live inventory item payload."""
    inv_item = (
        live_inv["inventoryItem"]
        if isinstance(live_inv.get("inventoryItem"), dict)
        else live_inv
    )
    prod = dict(inv_item.get("product") or {})
    aspects: dict[str, list[str]] = {
        k: list(v) if isinstance(v, list) else [str(v)]
        for k, v in (prod.get("aspects") or {}).items()
    }

    # Drop non-taxonomy duplicate 'Year'
    aspects.pop("Year", None)

    year_raw = meta.get("year") or (aspects.get("Year of Production") or [""])[0]
    year_int = extract_year_int(year_raw)

    # Normalize Year of Production
    if year_int is not None:
        aspects["Year of Production"] = [str(year_int)]
    elif year_raw:
        aspects["Year of Production"] = [str(year_raw)]

    # Map Time Period Manufactured
    tpm = map_time_period_manufactured(year_int)
    if tpm:
        aspects["Time Period Manufactured"] = [tpm]

    # Map Format from metadata.json photo_type
    photo_type = str(meta.get("photo_type") or "").strip()
    if photo_type and photo_type.lower() != "photograph":
        aspects["Format"] = [photo_type]
    elif "snapshot" in str(meta.get("title", "")).lower():
        aspects["Format"] = ["Snapshot"]
    elif photo_type:
        aspects["Format"] = [photo_type]

    # Dynamic Antique / Vintage
    if year_int is not None:
        aspects["Antique"] = ["Yes"] if year_int <= ANTIQUE_CUTOFF_YEAR else ["No"]
        aspects["Vintage"] = ["Yes"] if year_int <= VINTAGE_CUTOFF_YEAR else ["No"]
    else:
        aspects.setdefault("Antique", ["No"])
        aspects.setdefault("Vintage", ["Yes"])

    # Validate SELECTION_ONLY aspects against Taxonomy
    if "Image Orientation" in aspects:
        valid_orient = _validate_selection_only(
            "Image Orientation",
            aspects["Image Orientation"],
            taxonomy,
            fallback=["Portrait"],
        )
        if valid_orient:
            aspects["Image Orientation"] = valid_orient

    if "Country of Origin" in aspects:
        valid_country = _validate_selection_only(
            "Country of Origin",
            aspects["Country of Origin"],
            taxonomy,
            fallback=["United States"],
        )
        if valid_country:
            aspects["Country of Origin"] = valid_country

    for sel_name in ("Image Color", "Original/Licensed Reprint", "Unit of Sale", "Signed", "Vintage", "Antique"):
        if sel_name in aspects:
            checked = _validate_selection_only(sel_name, aspects[sel_name], taxonomy)
            if checked:
                aspects[sel_name] = checked

    new_title = fix_title_photo_keyword(prod.get("title") or meta.get("title") or "")
    new_desc = build_html_description({**meta, "title": new_title}, aspects)
    new_cond_desc = build_condition_description(meta, aspects)

    updated_prod = dict(prod)
    updated_prod["title"] = new_title
    updated_prod["description"] = new_desc
    updated_prod["aspects"] = aspects

    payload: dict[str, Any] = {
        "availability": inv_item.get("availability")
        or {"shipToLocationAvailability": {"quantity": 1}},
        "condition": inv_item.get("condition") or "USED_EXCELLENT",
        "conditionDescription": new_cond_desc,
        "product": updated_prod,
    }
    if inv_item.get("packageWeightAndSize"):
        payload["packageWeightAndSize"] = inv_item["packageWeightAndSize"]
    if inv_item.get("conditionDescriptors"):
        payload["conditionDescriptors"] = inv_item["conditionDescriptors"]

    return payload


def build_optimized_offer_payload(
    live_offer: dict[str, Any],
    target_price: Optional[str] = None,
    listing_description: Optional[str] = None,
) -> tuple[dict[str, Any], bool]:
    """Build PUT /offer/{offerId} payload preserving price by default, updating HTML desc, and removing Best Offer.

    Returns (payload, needs_update).
    """
    current_price = (
        live_offer.get("pricingSummary", {}).get("price", {}).get("value")
    )
    policies = dict(live_offer.get("listingPolicies") or {})
    had_best_offer = "bestOfferTerms" in policies
    policies.pop("bestOfferTerms", None)

    price_changed = target_price is not None and current_price != target_price
    desc_changed = (
        listing_description is not None
        and live_offer.get("listingDescription") != listing_description
    )
    needs_update = price_changed or had_best_offer or desc_changed

    payload: dict[str, Any] = {}
    for k in ALLOWED_PUT_OFFER_KEYS:
        if k in live_offer and live_offer[k] is not None:
            payload[k] = live_offer[k]

    payload["listingPolicies"] = policies
    if target_price is not None:
        pricing = dict(payload.get("pricingSummary") or {})
        pricing["price"] = {"value": target_price, "currency": "USD"}
        payload["pricingSummary"] = pricing
    if listing_description is not None:
        payload["listingDescription"] = listing_description

    return payload, needs_update


def _load_audit_records(root_dir: Path, target_dir: Optional[Path]) -> list[dict[str, Any]]:
    audit_path = root_dir / ".cache" / "ebay_live_audit.json"
    if not audit_path.exists():
        raise FileNotFoundError(
            f"Audit cache not found at {audit_path}. Run sync_ebay_live_status.py first."
        )
    data = json.loads(audit_path.read_text(encoding="utf-8"))
    records = data.get("records", [])
    if target_dir and target_dir.resolve() != root_dir.resolve():
        resolved = target_dir.resolve()
        records = [
            r
            for r in records
            if Path(r["folder_path"]).resolve() == resolved
            or resolved in Path(r["folder_path"]).resolve().parents
        ]
    return records


def _push_bulk_inventory(
    batch: list[tuple[dict[str, Any], dict[str, Any]]],
    token_mgr: TokenManager,
    api_host: str,
) -> dict[str, tuple[bool, int, str]]:
    """Send up to 25 items via POST /bulk_create_or_replace_inventory_item."""
    url = f"{api_host}/sell/inventory/v1/bulk_create_or_replace_inventory_item"
    requests_list = []
    for rec, inv_payload in batch:
        item_with_sku = dict(inv_payload)
        item_with_sku["sku"] = rec["sku"]
        item_with_sku["locale"] = "en_US"
        requests_list.append(item_with_sku)

    status, body = _request_with_retry(
        "POST", url, token_mgr, json_body={"requests": requests_list}
    )
    outcomes: dict[str, tuple[bool, int, str]] = {}
    if status in (200, 207) and isinstance(body, dict):
        for r in body.get("responses", []):
            sku = r.get("sku", "")
            code = int(r.get("statusCode", status))
            ok = 200 <= code < 300
            msg = "OK" if ok else json.dumps(r.get("errors") or r)
            outcomes[sku] = (ok, code, msg)
    else:
        err_msg = json.dumps(body)
        for rec, _ in batch:
            outcomes[rec["sku"]] = (False, status, err_msg)
    return outcomes


def _push_single_inventory(
    sku: str, inv_payload: dict[str, Any], token_mgr: TokenManager, api_host: str
) -> tuple[bool, int, str]:
    url = f"{api_host}/sell/inventory/v1/inventory_item/{requests_quote(sku)}"
    status, body = _request_with_retry("PUT", url, token_mgr, json_body=inv_payload)
    if 200 <= status < 300:
        return True, status, "OK"
    return False, status, json.dumps(body)


def requests_quote(val: str) -> str:
    import urllib.parse

    return urllib.parse.quote(val, safe="")


def run_optimizer(
    root_dir: Path,
    target_dir: Optional[Path] = None,
    sku_filter: Optional[str] = None,
    store: str = "photo_vault",
    env: str = "production",
    dry_run: bool = False,
    count: Optional[int] = None,
    use_audit_cache: bool = False,
    skip_inventory: bool = False,
    skip_offers: bool = False,
    tag_sold_metadata: bool = False,
    set_price: Optional[str] = None,
    delay: float = 0.15,
) -> dict[str, Any]:
    taxonomy_path = root_dir / ".cache" / "ebay_taxonomy_262421.json"
    taxonomy = load_taxonomy_constraints(taxonomy_path)
    if not taxonomy:
        print(f"[-] Warning: Taxonomy file not found at {taxonomy_path}. Selection validation disabled.")

    records = _load_audit_records(root_dir, target_dir)
    if sku_filter:
        records = [r for r in records if r.get("sku") == sku_filter]
    if not records:
        print("[-] No records matched target directory/SKU.")
        return {"active": 0, "skipped_non_active": 0}

    active_records = [r for r in records if r.get("classification") == "ACTIVE"]
    non_active_records = [r for r in records if r.get("classification") != "ACTIVE"]

    print("=" * 78)
    print(
        f"PHASE 1 DETERMINISTIC OPTIMIZER ({store.upper()} | {env.upper()} | "
        f"{'DRY-RUN' if dry_run else 'LIVE WRITE'})"
    )
    print("=" * 78)
    print(f"Total audited items  : {len(records)}")
    print(f"Strictly ACTIVE      : {len(active_records)}")
    print(f"Skipped (non-ACTIVE) : {len(non_active_records)}")

    if tag_sold_metadata and not dry_run:
        tagged = 0
        for r in non_active_records:
            cls = r.get("classification")
            if cls == "ERROR":
                continue
            meta_p = Path(r["folder_path"]) / "metadata.json"
            if meta_p.exists():
                try:
                    m = json.loads(meta_p.read_text(encoding="utf-8"))
                    changed = False
                    if cls == "SOLD" and not m.get("ebay_sold"):
                        m["ebay_sold"] = True
                        changed = True
                    if m.get("ebay_listing_status") != cls:
                        m["ebay_listing_status"] = cls
                        changed = True
                    if changed:
                        meta_p.write_text(json.dumps(m, indent=4), encoding="utf-8")
                        tagged += 1
                except Exception:
                    pass
        print(f"[*] Tagged {tagged} non-ACTIVE metadata.json files with live status.")

    if count is not None and count > 0:
        active_records = active_records[:count]
        print(f"[*] Limiting run to first {len(active_records)} ACTIVE item(s).")

    if dry_run and not use_audit_cache:
        print("[-] Note: --dry-run without --use-audit-cache still reads live audit cache.")

    token_mgr: Optional[TokenManager] = None
    api_host = ENVIRONMENTS[env]["api_host"]
    if not dry_run:
        token_mgr = TokenManager(store=store, env=env)
        token_mgr.get_token()

    inv_to_update = 0
    offers_needing_price = 0
    offers_needing_bo_removal = 0
    offers_needing_update = 0
    sample_diffs: list[dict[str, Any]] = []

    inv_ok = 0
    inv_fail = 0
    off_ok = 0
    off_fail = 0
    off_skipped_pending = 0
    live_skipped_guard = 0

    # Prepare and execute in chunks of 25 in live mode (or evaluate all in dry-run)
    if dry_run:
        for rec in active_records:
            folder = Path(rec["folder_path"])
            meta_path = folder / "metadata.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            live_inv = rec.get("live_inventory")
            live_offer = rec.get("live_offer")
            if not live_inv or not live_offer:
                continue

            new_inv = build_optimized_inventory_payload(live_inv, meta, taxonomy)
            _, offer_changed = build_optimized_offer_payload(
                live_offer,
                target_price=set_price,
                listing_description=new_inv["product"]["description"],
            )
            old_price = live_offer.get("pricingSummary", {}).get("price", {}).get("value")
            had_bo = "bestOfferTerms" in (live_offer.get("listingPolicies") or {})

            if not skip_inventory:
                inv_to_update += 1
            if not skip_offers and offer_changed:
                offers_needing_update += 1
                if set_price is not None and old_price != set_price:
                    offers_needing_price += 1
                if had_bo:
                    offers_needing_bo_removal += 1

            if len(sample_diffs) < 3:
                inv_item = (
                    live_inv["inventoryItem"]
                    if isinstance(live_inv.get("inventoryItem"), dict)
                    else live_inv
                )
                old_aspects = inv_item.get("product", {}).get("aspects", {})
                new_aspects = new_inv.get("product", {}).get("aspects", {})
                added_or_changed = {
                    k: new_aspects[k]
                    for k in new_aspects
                    if old_aspects.get(k) != new_aspects[k]
                }
                sample_diffs.append(
                    {
                        "rel_path": rec["rel_path"],
                        "sku": rec["sku"],
                        "old_title": inv_item.get("product", {}).get("title"),
                        "new_title": new_inv["product"]["title"],
                        "aspect_changes": added_or_changed,
                        "removed_aspects": [k for k in old_aspects if k not in new_aspects],
                        "html_desc_preview": new_inv["product"]["description"][:260] + "...",
                        "old_price": old_price,
                        "new_price": set_price if set_price is not None else old_price,
                        "had_best_offer": had_bo,
                        "will_have_best_offer": False,
                    }
                )
    elif token_mgr is not None:
        total_chunks = (len(active_records) + 24) // 25
        for chunk_idx in range(0, len(active_records), 25):
            chunk_recs = active_records[chunk_idx : chunk_idx + 25]
            chunk_num = chunk_idx // 25 + 1
            chunk_skus = [r["sku"] for r in chunk_recs]

            # 1. Batch-fetch live inventory (1 call per 25 SKUs)
            bulk_inv_map = fetch_bulk_inventory(chunk_skus, token_mgr, api_host)

            # 2. Concurrently re-fetch live offers (5 workers) for sold-safety check
            chunk_offers: dict[str, tuple[int, dict[str, Any]]] = {}
            with ThreadPoolExecutor(max_workers=5) as pool:
                futs = {
                    pool.submit(fetch_single_offer, r, token_mgr, api_host): r["sku"]
                    for r in chunk_recs
                }
                for fut in as_completed(futs):
                    s, c, body = fut.result()
                    chunk_offers[s] = (c, body)

            verified_inv_batch: list[tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any], Path]] = []

            for rec in chunk_recs:
                sku = rec["sku"]
                folder = Path(rec["folder_path"])
                meta_path = folder / "metadata.json"
                if not meta_path.exists():
                    continue
                meta = json.loads(meta_path.read_text(encoding="utf-8"))

                live_inv = bulk_inv_map.get(sku, {})
                off_code, live_offer = chunk_offers.get(sku, (0, {}))
                live_cls = classify_live_item(live_inv, off_code, live_offer)
                if live_cls != "ACTIVE":
                    live_skipped_guard += 1
                    print(f"  [GUARD SKIP] {sku} live status changed to {live_cls}; skipping.")
                    if live_cls == "SOLD":
                        meta["ebay_sold"] = True
                        meta["ebay_listing_status"] = "SOLD"
                        meta_path.write_text(json.dumps(meta, indent=4), encoding="utf-8")
                    continue

                new_inv = build_optimized_inventory_payload(live_inv, meta, taxonomy)
                if not skip_inventory:
                    inv_to_update += 1
                    verified_inv_batch.append((rec, new_inv, folder, meta, meta_path))

                if not skip_offers:
                    new_offer, offer_changed = build_optimized_offer_payload(
                        live_offer,
                        target_price=set_price,
                        listing_description=new_inv["product"]["description"],
                    )
                    if offer_changed:
                        offers_needing_update += 1
                        old_price = live_offer.get("pricingSummary", {}).get("price", {}).get("value")
                        if set_price is not None and old_price != set_price:
                            offers_needing_price += 1
                        if "bestOfferTerms" in (live_offer.get("listingPolicies") or {}):
                            offers_needing_bo_removal += 1
                        offer_id = rec.get("offer_id") or live_offer.get("offerId")
                        put_url = f"{api_host}/sell/inventory/v1/offer/{offer_id}"
                        status, body = _request_with_retry("PUT", put_url, token_mgr, json_body=new_offer)
                        if 200 <= status < 300:
                            off_ok += 1
                            (folder / "offer.json").write_text(
                                json.dumps(new_offer, indent=4), encoding="utf-8"
                            )
                        else:
                            msg = json.dumps(body)
                            if "best offer" in msg.lower() or "pending" in msg.lower():
                                off_skipped_pending += 1
                                print(f"  [OFFER WARN] {sku} pending offer blocked update: {msg}")
                            else:
                                off_fail += 1
                                print(f"  [OFFER FAIL] {sku} HTTP {status}: {msg}")
                        if delay > 0:
                            time.sleep(delay)

            # 3. Push verified-ACTIVE inventory items in bulk (or single if len == 1)
            if not skip_inventory and verified_inv_batch:
                if len(verified_inv_batch) == 1:
                    rec, new_inv, folder, meta, meta_path = verified_inv_batch[0]
                    ok, code, msg = _push_single_inventory(rec["sku"], new_inv, token_mgr, api_host)
                    outcomes = {rec["sku"]: (ok, code, msg)}
                else:
                    batch = [(r, inv_p) for r, inv_p, _, _, _ in verified_inv_batch]
                    outcomes = _push_bulk_inventory(batch, token_mgr, api_host)

                for rec, new_inv, folder, meta, meta_path in verified_inv_batch:
                    sku = rec["sku"]
                    ok, code, msg = outcomes.get(sku, (False, 0, "missing_response"))
                    if not ok and (code == 0 or code == 429 or code >= 500):
                        time.sleep(1.5)
                        ok, code, msg = _push_single_inventory(sku, new_inv, token_mgr, api_host)

                    if ok:
                        inv_ok += 1
                        (folder / "inventory_item.json").write_text(
                            json.dumps(new_inv, indent=4), encoding="utf-8"
                        )
                        new_title = new_inv["product"]["title"]
                        if new_title != meta.get("title"):
                            meta["title"] = new_title
                            meta_path.write_text(json.dumps(meta, indent=4), encoding="utf-8")
                    else:
                        inv_fail += 1
                        print(f"  [INV FAIL] {sku} HTTP {code}: {msg}")

            print(
                f"  [chunk {chunk_num}/{total_chunks}] "
                f"inv_ok={inv_ok} inv_fail={inv_fail} guard_skip={live_skipped_guard}"
            )
            if delay > 0:
                time.sleep(delay)

    print("\n" + "-" * 78)
    print("SUMMARY")
    print("-" * 78)
    print(f"ACTIVE items evaluated        : {len(active_records)}")
    print(f"Non-ACTIVE safely skipped     : {len(non_active_records)}")
    print(f"Inventory updates planned     : {inv_to_update}")
    print(f"Offers needing update         : {offers_needing_update}")
    if set_price is not None:
        print(f"  - Price -> ${set_price:<14}: {offers_needing_price}")
    else:
        print("  - Price updates             : Skipped (preserving live price)")
    print(f"  - Remove Best Offer         : {offers_needing_bo_removal}")
    if not dry_run:
        print(f"Live re-check guard skips     : {live_skipped_guard}")
        print(f"Inventory pushed OK / Fail    : {inv_ok} / {inv_fail}")
        print(f"Offers pushed OK / Warn / Fail: {off_ok} / {off_skipped_pending} / {off_fail}")

    if dry_run and sample_diffs:
        print("\n" + "=" * 78)
        print("DRY-RUN SAMPLE DIFFS (FIRST 3 ACTIVE ITEMS)")
        print("=" * 78)
        for idx, diff in enumerate(sample_diffs, start=1):
            print(f"\n--- Sample {idx}: {diff['rel_path']} (SKU: {diff['sku']}) ---")
            print(json.dumps(diff, indent=2))

    return {
        "active": len(active_records),
        "skipped_non_active": len(non_active_records),
        "inv_to_update": inv_to_update,
        "offers_needing_update": offers_needing_update,
        "offers_needing_price": offers_needing_price,
        "offers_needing_bo_removal": offers_needing_bo_removal,
        "inv_ok": inv_ok,
        "inv_fail": inv_fail,
        "off_ok": off_ok,
        "off_fail": off_fail,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 1 Deterministic eBay SEO & Best Offer Removal (preserves live price by default)."
    )
    parser.add_argument("--dir", help="Optional target directory filter", default=None)
    parser.add_argument("--sku", help="Optional exact SKU filter", default=None)
    parser.add_argument("--store", default="photo_vault", help="eBay store key")
    parser.add_argument(
        "--env", choices=["sandbox", "production"], default="production"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--use-audit-cache", action="store_true")
    parser.add_argument("--skip-inventory", action="store_true")
    parser.add_argument("--skip-offers", action="store_true")
    parser.add_argument("--tag-sold-metadata", action="store_true")
    parser.add_argument(
        "--set-price",
        default=None,
        help="Optional target price (e.g. '24.99'); omitted by default to preserve live prices",
    )
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()

    root_dir = Path(__file__).parent.resolve()
    target_dir = Path(args.dir).resolve() if args.dir else None
    run_optimizer(
        root_dir=root_dir,
        target_dir=target_dir,
        sku_filter=args.sku,
        store=args.store,
        env=args.env,
        dry_run=args.dry_run,
        count=args.count,
        use_audit_cache=args.use_audit_cache,
        skip_inventory=args.skip_inventory,
        skip_offers=args.skip_offers,
        tag_sold_metadata=args.tag_sold_metadata,
        set_price=args.set_price,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()

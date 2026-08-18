"""Master execution script for eBay photo processing & publishing workflow.

This script consolidates all post-metadata tasks for scanned photograph folders:
1. Uploads front and back JPEG images to eBay Media API (getting real HTTPS image URLs).
2. Generates local inventory_item.json with rich eBay aspects and static defaults.
3. Sends PUT /sell/inventory/v1/inventory_item/{sku} to eBay API.
4. Generates local offer.json using metadata price and eBay business policies.
5. Sends POST /sell/inventory/v1/offer to eBay API (retrieving offerId).
6. Sends POST /sell/inventory/v1/offer/{offerId}/publish to make the listing live.
7. Updates metadata.json state flags after each successful step for atomic resume capability.
8. Updates listing_log.md with all live listings.

Usage:
  python run_ebay_workflow.py /path/to/EPSCAN                     # Process all unprocessed folders in parent directory
  python run_ebay_workflow.py /path/to/EPSCAN/215103_0001         # Process single item folder (for targeted retry)
  python run_ebay_workflow.py /path/to/EPSCAN --env production    # Run against eBay Production environment (default)
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
import requests

from typing import Any
from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS

DEFAULT_BUSINESS_POLICIES: dict[str, Any] = {
    "fulfillmentPolicyId": "251347744026",
    "returnPolicyId": "251347661026",
    "paymentPolicyId": "251347757026"
}

def build_ebay_safe_sku(folder_name: str) -> str:
    """Generates an eBay-safe SKU matching the parent folder name (preserving hyphens and underscores)."""
    return re.sub(r"[^A-Za-z0-9_\-]", "", folder_name)[:50]

def is_valid_ebay_image(url: str) -> bool:
    """Checks if an eBay image URL is valid and non-placeholder (size > 5000 bytes)."""
    if not url or not url.startswith("https://") or "example.com" in url:
        return False
    try:
        resp = requests.get(url, timeout=4)
        if resp.status_code == 200 and len(resp.content) > 5000:
            return True
    except Exception:
        pass
    return False

def upload_image(file_path: Path, access_token: str, media_api_host: str) -> str | None:
    """Uploads a single image file to eBay Media API and returns the image URL."""
    upload_url = f"{media_api_host}/commerce/media/v1_beta/image/create_image_from_file"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    
    print(f"    [*] Uploading {file_path.name} to eBay Media API...")
    
    try:
        with open(file_path, "rb") as f:
            files = {"image": (file_path.name, f, "image/jpeg")}
            response = requests.post(upload_url, headers=headers, files=files)
            
        if response.status_code == 201:
            data = response.json()
            ebay_url = data.get("maxDimensionImageUrl") or data.get("imageUrl")
            if ebay_url:
                print(f"    [+] Uploaded: {file_path.name} -> {ebay_url}")
                return ebay_url
                
            location = response.headers.get("Location")
            if location:
                match = re.search(r"/image/([^/]+)", location)
                if match:
                    image_id = match.group(1)
                    get_url = f"{media_api_host}/commerce/media/v1_beta/image/{image_id}"
                    get_resp = requests.get(get_url, headers=headers)
                    if get_resp.status_code == 200:
                        get_data = get_resp.json()
                        ebay_url = get_data.get("maxDimensionImageUrl") or get_data.get("imageUrl")
                        if ebay_url:
                            print(f"    [+] Uploaded: {file_path.name} -> {ebay_url}")
                            return ebay_url
            print(f"    [-] Warning: Upload succeeded but could not extract image URL for {file_path.name}.")
            return None
        else:
            print(f"    [-] Failed to upload {file_path.name}. Status: {response.status_code}")
            print(f"        Details: {response.text}")
            return None
    except Exception as e:
        print(f"    [-] Exception uploading {file_path.name}: {e}")
        return None

def build_inventory_item_payload(folder_path: Path, meta: dict, image_urls: list) -> dict:
    """Builds the inventory_item payload using metadata and uploaded image URLs."""
    size_str = meta.get("size", "4x6in")
    orientation = "Portrait"
    match = re.match(r"([\d\.]+)\s*x\s*([\d\.]+)", size_str.lower())
    if match:
        width = float(match.group(1))
        height = float(match.group(2))
        if width > height:
            orientation = "Landscape"
        elif width == height:
            orientation = "Square"
        else:
            orientation = "Portrait"

    # Build description combining main description and back transcription if available
    desc = meta.get("description", "")
    back_ocr = meta.get("back_text_transcription")
    if back_ocr:
        desc += f" Back of Photo Note:\"{back_ocr}\""

    subjects = meta.get("subjects") or meta.get("subject") or []
    if not isinstance(subjects, list):
        subjects = [subjects]

    # Build aspects
    aspects = {
        "Subject": subjects,
        "Year of Production": [meta.get("year", "Unknown")],
        "Year": [meta.get("year", "Unknown")],
        "Unit of Sale": ["Single Piece"],
        "Size": [size_str],
        "Vintage": ["Yes"],
        "Antique": ["No"],
        "Signed": ["No"],
        "Material": ["Paper"],
        "Production Technique": ["Gelatin-Silver Print"],
        "Country of Origin": ["United States"],
        "Number of Photographs": ["1"],
        "Image Orientation": [orientation],
        "Image Color": [meta.get("image_color", "Black & White")],
        "Type": ["Photograph"],
        "Original/Licensed Reprint": ["Original"],
        "Style": ["Vernacular / Candid"],
        "Features": ["One of a Kind (OOAK)"],
        "Framing": ["Unframed"],
        "Size Type/Largest Dimension": ['Small (Up to 7")'],
        "Listed By": ["Dealer or Reseller"],
    }

    if "decade" in meta and meta["decade"]:
        aspects["Decade"] = [meta["decade"]]

    if "location" in meta and meta["location"]:
        aspects["Location"] = [meta["location"]]

    themes = meta.get("themes") or meta.get("theme")
    if themes:
        aspects["Theme"] = themes if isinstance(themes, list) else [themes]

    raw_title = meta.get("title", "").strip()
    if raw_title and "photo" not in raw_title.lower():
        raw_title = f"{raw_title} Photo"
    title_val = raw_title[:80]

    inventory_item = {
        "product": {
            "title": title_val,
            "description": desc,
            "aspects": aspects,
            "imageUrls": image_urls,
        },
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "condition": "USED_EXCELLENT",
        "conditionDescription": f"Vintage photograph. Year: {meta.get('year', 'Unknown')}. Size: {size_str}.",
        "packageWeightAndSize": {
            "dimensions": {
                "length": 7.0,
                "width": 5.0,
                "height": 0.125,
                "unit": "INCH",
            },
            "packageType": "LETTER",
            "weight": {
                "value": 1.0,
                "unit": "OUNCE",
            },
        },
    }
    return inventory_item

def fetch_business_policies(access_token: str, api_host: str, store: str) -> dict[str, Any]:
    """Fetches active Business Policy IDs for the specific store from eBay Account API."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    policies = dict(DEFAULT_BUSINESS_POLICIES)
    print(f"[*] Synchronizing Business Policies from eBay for store '{store}'...")

    try:
        url = f"{api_host}/sell/account/v1/fulfillment_policy?marketplace_id=EBAY_US"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            items = resp.json().get("fulfillmentPolicies", [])
            if items:
                default_pol = next((p for p in items if p.get("categoryTypes", [{}])[0].get("default")), items[0])
                pid = default_pol.get("fulfillmentPolicyId")
                if pid:
                    policies["fulfillmentPolicyId"] = pid
                    print(f"    [+] Fulfillment Policy: {default_pol.get('name')} (ID: {pid})")
    except Exception as e:
        print(f"    [-] Could not auto-fetch fulfillment policy for '{store}': {e}")

    try:
        url = f"{api_host}/sell/account/v1/return_policy?marketplace_id=EBAY_US"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            items = resp.json().get("returnPolicies", [])
            if items:
                default_pol = next((p for p in items if p.get("categoryTypes", [{}])[0].get("default")), items[0])
                pid = default_pol.get("returnPolicyId")
                if pid:
                    policies["returnPolicyId"] = pid
                    print(f"    [+] Return Policy: {default_pol.get('name')} (ID: {pid})")
    except Exception as e:
        print(f"    [-] Could not auto-fetch return policy for '{store}': {e}")

    try:
        url = f"{api_host}/sell/account/v1/payment_policy?marketplace_id=EBAY_US"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            items = resp.json().get("paymentPolicies", [])
            if items:
                default_pol = next((p for p in items if p.get("categoryTypes", [{}])[0].get("default")), items[0])
                pid = default_pol.get("paymentPolicyId")
                if pid:
                    policies["paymentPolicyId"] = pid
                    print(f"    [+] Payment Policy: {default_pol.get('name')} (ID: {pid})")
    except Exception as e:
        print(f"    [-] Could not auto-fetch payment policy for '{store}': {e}")

    return policies

def build_offer_payload(folder_path: Path, meta: dict, business_policies: dict[str, Any], auto_accept_pct: float = 80.0, auto_decline_pct: float = 50.0) -> dict:
    """Builds the offer payload using metadata, store-specific business policies, and best offer terms (auto-accept & auto-decline)."""
    sku = meta.get("sku", folder_path.name)
    price_str = str(meta.get("price", "10")).replace('$', '').replace(',', '').strip()
    try:
        price_num = float(price_str)
        price_val = f"{price_num:.2f}"
    except ValueError:
        price_num = 10.00
        price_val = "10.00"

    auto_accept_num = round(price_num * (auto_accept_pct / 100.0), 2)
    auto_accept_val = f"{auto_accept_num:.2f}"

    auto_decline_num = round(price_num * (auto_decline_pct / 100.0), 2)
    auto_decline_val = f"{auto_decline_num:.2f}"

    listing_policies: dict[str, Any] = dict(business_policies)
    listing_policies["bestOfferTerms"] = {
        "bestOfferEnabled": True,
        "autoAcceptPrice": {
            "value": auto_accept_val,
            "currency": "USD"
        },
        "autoDeclinePrice": {
            "value": auto_decline_val,
            "currency": "USD"
        }
    }

    offer_data = {
        "sku": sku,
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "categoryId": "262421",
        "merchantLocationKey": "HOME_BASE",
        "pricingSummary": {
            "price": {
                "value": price_val,
                "currency": "USD"
            }
        },
        "listingPolicies": listing_policies
    }
    return offer_data

def process_single_item(folder_path: Path, access_token: str, api_host: str, media_api_host: str, business_policies: dict[str, Any], auto_accept_pct: float = 80.0, auto_decline_pct: float = 60.0) -> dict:
    """Executes the full pipeline for a single photo item folder with atomic state updates."""
    print(f"\n--- Processing Item: {folder_path.name} ---")
    stats = {"success": False}
    
    metadata_path = folder_path / "metadata.json"
    if not metadata_path.exists():
        print(f" [-] Error: metadata.json not found in {folder_path.name}")
        return stats

    with open(metadata_path, "r") as f:
        meta = json.load(f)

    sku = meta.get("sku")
    expected_sku = build_ebay_safe_sku(folder_path.name)
    if not meta.get("ebay_inventory_created") and sku != expected_sku:
        sku = expected_sku
        meta["sku"] = sku
        with open(metadata_path, "w") as f:
            json.dump(meta, f, indent=4)
    elif not sku:
        sku = expected_sku
    image_files = sorted([
        f for f in folder_path.iterdir()
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg")
    ])

    # Step 1: Image Uploading & Local inventory_item.json Generation
    item_json_path = folder_path / "inventory_item.json"
    image_urls = []
    
    if item_json_path.exists():
        try:
            with open(item_json_path, "r") as f:
                existing_item = json.load(f)
                existing_urls = existing_item.get("product", {}).get("imageUrls", [])
                if existing_urls and all(is_valid_ebay_image(url) for url in existing_urls):
                    image_urls = existing_urls
                elif existing_urls:
                    print(" -> Step 1: Detected dead/placeholder eBay image URL(s). Re-uploading local images...")
        except Exception:
            image_urls = []

    if not image_urls:
        print(" -> Step 1: Uploading local images to eBay Media API...")
        for img_file in image_files:
            ebay_url = upload_image(img_file, access_token, media_api_host)
            if ebay_url:
                image_urls.append(ebay_url)

        if not image_urls:
            print(f" [!] Step 1 Failed: Could not upload images for {folder_path.name}.")
            return stats

    # Save local inventory_item.json
    inv_payload = build_inventory_item_payload(folder_path, meta, image_urls)
    with open(item_json_path, "w") as f:
        json.dump(inv_payload, f, indent=4)
    print(f"    [+] Saved/Updated local {item_json_path.name}")

    # Step 2: Create Inventory Item on eBay
    if not meta.get("ebay_inventory_created"):
        print(" -> Step 2: Creating Inventory Item on eBay (PUT)...")
        put_url = f"{api_host}/sell/inventory/v1/inventory_item/{sku}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Language": "en-US",
            "Content-Type": "application/json"
        }
        resp = requests.put(put_url, headers=headers, json=inv_payload)
        if resp.status_code in (200, 201, 204):
            print(f"    [+] Inventory item created/updated for SKU {sku}")
            meta["ebay_inventory_created"] = True
            with open(metadata_path, "w") as f:
                json.dump(meta, f, indent=4)
        else:
            print(f" [!] Step 2 Failed (PUT inventory_item). Status: {resp.status_code}\n    Details: {resp.text}")
            return stats
    else:
        print(" -> Step 2: Inventory item already exists on eBay (Skipping).")

    # Step 3: Create Local offer.json (with Best Offer) & Create Offer on eBay
    offer_json_path = folder_path / "offer.json"
    offer_payload = build_offer_payload(folder_path, meta, business_policies=business_policies, auto_accept_pct=auto_accept_pct, auto_decline_pct=auto_decline_pct)
    with open(offer_json_path, "w") as f:
        json.dump(offer_payload, f, indent=4)

    if not meta.get("ebay_offer_created"):
        print(f" -> Step 3: Creating Offer on eBay with Best Offer enabled ({auto_accept_pct}% auto-accept / {auto_decline_pct}% auto-decline)...")
        post_url = f"{api_host}/sell/inventory/v1/offer"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Language": "en-US",
            "Content-Type": "application/json"
        }
        resp = requests.post(post_url, headers=headers, json=offer_payload)
        if resp.status_code == 201:
            offer_id = resp.json().get("offerId")
            print(f"    [+] Offer created successfully. Offer ID: {offer_id}")
            meta["ebay_offer_created"] = True
            meta["ebay_offer_id"] = offer_id
            with open(metadata_path, "w") as f:
                json.dump(meta, f, indent=4)
        else:
            print(f" [!] Step 3 Failed (POST offer). Status: {resp.status_code}\n    Details: {resp.text}")
            return stats
    else:
        print(f" -> Step 3: Offer already created (Offer ID: {meta.get('ebay_offer_id')}) (Skipping).")

    # Step 4: Publish Offer on eBay
    if not meta.get("ebay_offer_published"):
        print(" -> Step 4: Publishing Offer on eBay (POST)...")
        offer_id = meta.get("ebay_offer_id")
        pub_url = f"{api_host}/sell/inventory/v1/offer/{offer_id}/publish"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Language": "en-US",
            "Content-Type": "application/json"
        }
        resp = requests.post(pub_url, headers=headers)
        
        # Self-healing retry for transient 500 / 25001 eBay internal backend glitches
        if resp.status_code == 500 and "25001" in resp.text:
            print("    [*] Received transient eBay 500 error (25001). Retrying publish in 2 seconds...")
            import time
            time.sleep(2)
            resp = requests.post(pub_url, headers=headers)
        # Self-healing handler for 25604 (Product not found): Re-sync inventory item on eBay and retry publish
        elif resp.status_code == 500 and "25604" in resp.text:
            print("    [*] Received 25604 (Product not found). Re-syncing inventory item on eBay (PUT)...")
            put_url = f"{api_host}/sell/inventory/v1/inventory_item/{sku}"
            put_resp = requests.put(put_url, headers=headers, json=inv_payload)
            if put_resp.status_code in (200, 201, 204):
                print(f"    [+] Re-synced inventory item for SKU {sku}. Retrying publish...")
                import time
                time.sleep(1)
                resp = requests.post(pub_url, headers=headers)

        if resp.status_code == 200:
            res_data = resp.json()
            listing_id = res_data.get("listingId")
            domain = "www.sandbox.ebay.com" if "sandbox" in api_host else "www.ebay.com"
            listing_url = f"https://{domain}/itm/{listing_id}" if listing_id else None
            print(f"    [+] Successfully Published! Listing URL: {listing_url}")
            meta["ebay_offer_published"] = True
            meta["ebay_listing_url"] = listing_url
            if "ebay_listing_id" in meta:
                del meta["ebay_listing_id"]
            with open(metadata_path, "w") as f:
                json.dump(meta, f, indent=4)
            stats["success"] = True
        else:
            print(f" [!] Step 4 Failed (POST publish). Status: {resp.status_code}\n    Details: {resp.text}")
            return stats
    else:
        print(f" -> Step 4: Offer already published ({meta.get('ebay_listing_url')}) (Skipping).")
        stats["success"] = True

    print(f" [SUCCESS] Workflow complete for {folder_path.name}!")
    return stats

def find_epscan_root(path: Path) -> Path:
    """Finds the root parent directory containing 'EPSCAN' in its name."""
    curr = path.resolve()
    epscan_root = None
    while True:
        if "EPSCAN" in curr.name.upper():
            epscan_root = curr
        if curr.parent == curr:
            break
        curr = curr.parent
    return epscan_root or path.resolve()

def update_markdown_log(target_dir: Path):
    """Generates an updated single markdown listing log at the root EPSCAN directory."""
    root_dir = find_epscan_root(target_dir)
    log_path = root_dir / "listing_log.md"
    
    # Clean up any nested listing_log.md inside cheap_photos or subfolders
    for nested_log in root_dir.rglob("listing_log.md"):
        if nested_log.resolve() != log_path.resolve():
            try:
                os.remove(nested_log)
            except Exception:
                pass

    total_folders = 0
    meta_generated_count = 0
    inventory_created_count = 0
    offer_created_count = 0
    published_count = 0
    
    item_rows = []
    
    # Collect all product folders recursively under root_dir
    candidate_folders = []
    for root, dirs, files in os.walk(root_dir):
        r_path = Path(root)
        if r_path.resolve() == root_dir.resolve() or r_path.name.startswith("."):
            continue
        jpg_files = [f for f in files if Path(f).suffix.lower() in ('.jpg', '.jpeg')]
        if (r_path / "metadata.json").exists() or len(jpg_files) == 2:
            candidate_folders.append(r_path)

    candidate_folders = sorted(candidate_folders, key=lambda p: str(p.relative_to(root_dir)))

    for item in candidate_folders:
        total_folders += 1
        meta_path = item / "metadata.json"
        rel_path = item.relative_to(root_dir)
        folder_link = f"[{rel_path}](./{rel_path})"
        
        if meta_path.exists():
            meta_generated_count += 1
            try:
                with open(meta_path, "r") as m:
                    meta = json.load(m)
                    
                inv_created = bool(meta.get("ebay_inventory_created"))
                offer_created = bool(meta.get("ebay_offer_created"))
                offer_id = meta.get("ebay_offer_id", "-")
                published = bool(meta.get("ebay_offer_published"))
                listing_url = meta.get("ebay_listing_url")
                
                if inv_created:
                    inventory_created_count += 1
                if offer_created:
                    offer_created_count += 1
                if published:
                    published_count += 1
                    
                meta_status = "✅"
                inv_status = "✅" if inv_created else "❌"
                offer_status = "✅" if offer_created else "❌"
                
                if published and listing_url:
                    pub_status = f"[View Listing]({listing_url})"
                elif published:
                    pub_status = "✅"
                else:
                    pub_status = "❌"
                    
                item_rows.append((folder_link, meta_status, inv_status, offer_status, pub_status))
            except Exception:
                item_rows.append((folder_link, "✅", "❌", "❌", "❌"))
        else:
            item_rows.append((folder_link, "❌", "❌", "❌", "❌"))

    pub_percentage = (published_count / total_folders * 100) if total_folders > 0 else 0.0

    with open(log_path, "w") as f:
        f.write(f"# eBay Listing Log ({root_dir.name})\n\n")
        f.write("### Summary Totals\n")
        f.write(f"- **Total Folders:** {total_folders}\n")
        f.write(f"- **Metadata Generated:** {meta_generated_count}\n")
        f.write(f"- **Inventory Items Created:** {inventory_created_count}\n")
        f.write(f"- **Offers Created:** {offer_created_count}\n")
        f.write(f"- **Listings Published:** {published_count}\n")
        f.write(f"- **Percentage Published:** {pub_percentage:.1f}%\n\n")
        
        f.write("## Listing Status Dashboard\n\n")
        if item_rows:
            f.write("| Folder / SKU | Metadata | Inventory Item | Offer ID | eBay Listing URL |\n")
            f.write("|--------------|----------|----------------|----------|------------------|\n")
            for row in item_rows:
                f.write(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} |\n")
        else:
            f.write("*No items found.*\n")
            
    print(f"\n[*] Updated single listing log at {log_path.resolve()}")

def main():
    parser = argparse.ArgumentParser(description="Master script to run the full eBay listing workflow.")
    parser.add_argument("directory", help="Target directory (parent folder like EPSCAN, or single item folder)")
    parser.add_argument("--store", help="eBay Store Account to use (defaults to EBAY_STORE or photo_vault)")
    parser.add_argument("--env", choices=["sandbox", "production"], help="eBay environment to use (defaults to EBAY_ENV or production)")
    parser.add_argument("--count", type=int, default=None, help="Maximum number of items to process")
    parser.add_argument("--best-offer-pct", type=float, default=80.0, help="Auto-accept percentage for Best Offer (default: 80.0)")
    parser.add_argument("--auto-decline-pct", type=float, default=50.0, help="Auto-decline percentage for Best Offer (default: 50.0)")
    args = parser.parse_args()
    
    target_dir = Path(args.directory).resolve()
    if not target_dir.exists():
        print(f"[-] Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)
        
    store = args.store or os.environ.get("EBAY_STORE", "photo_vault")
    env = args.env or os.environ.get("EBAY_ENV", "production").lower()
    if env not in ENVIRONMENTS:
        env = "production"
        
    api_host = ENVIRONMENTS[env]["api_host"]
    media_api_host = "https://apim.ebay.com" if env == "production" else api_host
    
    print("=" * 60)
    print(f"        eBay Master Listing Workflow ({store.upper()} | {env.upper()})")
    print("=" * 60)
    
    access_token = check_and_get_token(store=store, env=env)
    if not access_token:
        print(f"[-] Error: Could not get a valid access token for Store '{store}' ({env.upper()}). Run 'python ebay_oauth_helper.py --store {store} --env {env}' to log in.")
        sys.exit(1)

    # Fetch store-specific business policies from eBay
    business_policies = fetch_business_policies(access_token, api_host, store)

    raw_items = []
    if (target_dir / "metadata.json").exists() and target_dir.name != "cheap_photos":
        raw_items.append(target_dir)
    else:
        for item in sorted(target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith(".") and item.name != "cheap_photos":
                raw_items.append(item)

    items_to_process = []
    for item in raw_items:
        if (item / "metadata.json").exists():
            with open(item / "metadata.json", "r") as f:
                try:
                    meta = json.load(f)
                    if not meta.get("ebay_offer_published"):
                        items_to_process.append(item)
                except Exception:
                    pass

    if not items_to_process:
        print(f"[-] No unprocessed photo folders found in {target_dir}")
        sys.exit(0)

    if args.count is not None and args.count > 0:
        items_to_process = items_to_process[:args.count]
        print(f"[*] Limiting execution to {args.count} item(s) as requested.")
        
    print(f"[*] Found {len(items_to_process)} unprocessed item(s) to process.")

    success_count = 0
    failed_count = 0

    try:
        for item_dir in items_to_process:
            res = process_single_item(item_dir, access_token, api_host, media_api_host, business_policies=business_policies, auto_accept_pct=args.best_offer_pct, auto_decline_pct=args.auto_decline_pct)
            if res["success"]:
                success_count += 1
            else:
                failed_count += 1
    finally:
        print("\n" + "=" * 60)
        print(f"Workflow Summary: Successful: {success_count} | Failed: {failed_count}")
        print("=" * 60)
        update_markdown_log(target_dir)

if __name__ == "__main__":
    main()

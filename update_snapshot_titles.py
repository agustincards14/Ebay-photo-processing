"""Script to update inventory_item.json titles ending with 'snapshot' and update eBay Inventory API.

Recursively searches a given parent folder for `inventory_item.json` files.
For any item where `product.title` ends with 'snapshot' (case-insensitive) and
does not contain the word 'photo', it appends 'Photo' to the title (e.g., '... Snapshot Photo'),
updates the local JSON files (both inventory_item.json and metadata.json), and sends a PUT request
to the eBay Inventory API to update the live inventory item on eBay.

Usage:
  python update_snapshot_titles.py /path/to/EPSCAN_7_26
  python update_snapshot_titles.py /path/to/EPSCAN_7_30 --env production
  python update_snapshot_titles.py /path/to/EPSCAN_7_26 --dry-run
  python update_snapshot_titles.py /path/to/EPSCAN_7_26/AIPH-1-A/AIPH-1-A1 --count 1
"""

import os
import sys
import json
import re
import time
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import requests

from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS


def build_ebay_safe_sku(folder_name: str) -> str:
    """Generates an eBay-safe SKU matching the folder name (preserving hyphens and underscores)."""
    return re.sub(r"[^A-Za-z0-9_\-]", "", folder_name)[:50]


def find_inventory_item_files(target_path: Path) -> List[Path]:
    """Recursively finds all inventory_item.json files within target_path."""
    if not target_path.exists():
        return []

    if target_path.is_file():
        if target_path.name == "inventory_item.json" or target_path.suffix.lower() == ".json":
            return [target_path]
        return []

    # If it's a directory, check if it directly contains inventory_item.json or recurse
    found_files = list(target_path.rglob("inventory_item.json"))
    return sorted(list(set(found_files)), key=lambda p: str(p))


def needs_photo_suffix(title: str) -> bool:
    """Checks if title ends with 'snapshot' (case-insensitive) and does NOT contain 'photo'."""
    if not title:
        return False
    clean_title = title.strip().lower()
    return clean_title.endswith("snapshot") and "photo" not in clean_title


def update_ebay_inventory_item(
    sku: str,
    payload: Dict[str, Any],
    access_token: str,
    api_host: str,
    max_retries: int = 2
) -> Tuple[bool, int, str]:
    """Sends PUT /sell/inventory/v1/inventory_item/{sku} to eBay Sell Inventory API."""
    url = f"{api_host}/sell/inventory/v1/inventory_item/{sku}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Language": "en-US",
        "Content-Type": "application/json"
    }

    for attempt in range(max_retries + 1):
        try:
            resp = requests.put(url, headers=headers, json=payload, timeout=15)
            if resp.status_code in (200, 201, 204):
                return True, resp.status_code, "OK"
            elif resp.status_code == 429:
                wait_sec = 2 ** (attempt + 1)
                time.sleep(wait_sec)
                continue
            elif resp.status_code >= 500 and attempt < max_retries:
                time.sleep(2)
                continue
            else:
                return False, resp.status_code, resp.text
        except requests.RequestException as e:
            if attempt < max_retries:
                time.sleep(2)
                continue
            return False, 0, str(e)

    return False, 0, "Max retries exceeded"


def process_inventory_files(
    target_dir: Path,
    store: str = "photo_vault",
    env: str = "production",
    count: Optional[int] = None,
    dry_run: bool = False,
    delay: float = 0.2,
    skip_ebay: bool = False
):
    print("=" * 80)
    print(f"        eBay Snapshot Title Updater ({store.upper()} | {env.upper()})")
    print("=" * 80)
    print(f"Target Directory : {target_dir}")
    print(f"Dry Run Mode     : {'YES (No changes will be written or sent)' if dry_run else 'NO (Live execution)'}")
    print(f"Skip eBay API    : {'YES' if skip_ebay else 'NO'}")
    print("-" * 80)

    # 1. Discover all inventory_item.json files recursively
    print("[*] Scanning directory for inventory_item.json files...")
    all_files = find_inventory_item_files(target_dir)
    print(f"[+] Found {len(all_files)} total inventory_item.json file(s).")

    if not all_files:
        print("[-] No inventory_item.json files found to process.")
        return

    # 2. Filter matching files
    candidates: List[Tuple[Path, str, str, str]] = []
    for item_file in all_files:
        try:
            with open(item_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            title = data.get("product", {}).get("title", "")
            if needs_photo_suffix(title):
                # Construct new title
                new_title = f"{title.rstrip()} Photo"
                if len(new_title) > 80:
                    new_title = new_title[:80]
                
                # Determine SKU
                folder_path = item_file.parent
                meta_path = folder_path / "metadata.json"
                sku = None
                if meta_path.exists():
                    try:
                        with open(meta_path, "r", encoding="utf-8") as mf:
                            m = json.load(mf)
                        sku = m.get("sku")
                    except Exception:
                        pass
                if not sku:
                    sku = build_ebay_safe_sku(folder_path.name)

                candidates.append((item_file, sku, title, new_title))
        except Exception as e:
            print(f"[-] Warning: Failed to read {item_file}: {e}")

    total_matching = len(candidates)
    print(f"[+] Found {total_matching} item(s) ending with 'snapshot' without 'photo'.")

    if total_matching == 0:
        print("[*] All items already contain 'photo' or do not end with 'snapshot'. Nothing to update.")
        return

    if count is not None and count > 0:
        candidates = candidates[:count]
        print(f"[*] Processing limit applied: running first {len(candidates)} item(s).")

    # 3. Authentication for eBay API (unless dry_run or skip_ebay)
    access_token = None
    api_host = ENVIRONMENTS.get(env, {}).get("api_host", "https://api.ebay.com")

    if not dry_run and not skip_ebay:
        print("[*] Authenticating with eBay OAuth...")
        access_token = check_and_get_token(store=store, env=env)
        if not access_token:
            print(f"[-] Error: Could not get a valid access token for store '{store}' ({env.upper()}).")
            print(f"    Please run 'python ebay_oauth_helper.py --store {store} --env {env}' to log in.")
            sys.exit(1)
        print("[+] eBay OAuth Authentication successful.\n")

    # 4. Process each item with progress logging
    print("=" * 80)
    print(f"{'PROGRESS':<12} | {'SKU':<20} | {'STATUS'}")
    print("=" * 80)

    start_time = time.time()
    success_count = 0
    failed_count = 0
    skipped_count = 0

    for idx, (item_path, sku, old_title, new_title) in enumerate(candidates, start=1):
        folder_dir = item_path.parent
        pct = (idx / len(candidates)) * 100
        progress_str = f"[{idx}/{len(candidates)}] ({pct:5.1f}%)"

        print(f"\n{progress_str} Item: {folder_dir.name} (SKU: {sku})")
        print(f"  ├─ Current Title : \"{old_title}\"")
        print(f"  ├─ New Title     : \"{new_title}\" (Length: {len(new_title)} chars)")

        if dry_run:
            print(f"  └─ [DRY RUN] Would update local JSONs and eBay inventory item for SKU '{sku}'.")
            success_count += 1
            continue

        try:
            # 4a. Update local inventory_item.json
            with open(item_path, "r", encoding="utf-8") as f:
                item_data = json.load(f)

            if "product" not in item_data:
                item_data["product"] = {}
            item_data["product"]["title"] = new_title

            with open(item_path, "w", encoding="utf-8") as f:
                json.dump(item_data, f, indent=4)
            print(f"  ├─ Local File    : ✅ Saved {item_path.name}")

            # 4b. Update local metadata.json if present
            meta_path = folder_dir / "metadata.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta_data = json.load(mf)
                    meta_data["title"] = new_title
                    with open(meta_path, "w", encoding="utf-8") as mf:
                        json.dump(meta_data, mf, indent=4)
                    print(f"  ├─ Metadata File : ✅ Saved metadata.json")
                except Exception as me:
                    print(f"  ├─ Metadata File : ⚠️ Warning updating metadata.json: {me}")

            # 4c. Update eBay Inventory Item
            if not skip_ebay and access_token:
                ok, status_code, msg = update_ebay_inventory_item(
                    sku=sku,
                    payload=item_data,
                    access_token=access_token,
                    api_host=api_host
                )
                if ok:
                    print(f"  └─ eBay Inventory: ✅ Updated successfully (HTTP {status_code})")
                    success_count += 1
                else:
                    print(f"  └─ eBay Inventory: ❌ Failed (HTTP {status_code}): {msg[:120]}")
                    failed_count += 1
            else:
                print(f"  └─ eBay Inventory: ⏩ Skipped (Skip eBay enabled)")
                success_count += 1

        except Exception as e:
            print(f"  └─ Processing Err: ❌ Exception: {e}")
            failed_count += 1

        if delay > 0 and not dry_run and not skip_ebay:
            time.sleep(delay)

    elapsed = time.time() - start_time
    print("\n" + "=" * 80)
    print("                           EXECUTION SUMMARY")
    print("=" * 80)
    print(f"Total Scanned Files  : {len(all_files)}")
    print(f"Matching Criteria    : {total_matching}")
    print(f"Items Processed      : {len(candidates)}")
    print(f"Successful Updates   : {success_count}")
    print(f"Failed Updates       : {failed_count}")
    print(f"Elapsed Time         : {elapsed:.2f}s")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Recursively find inventory_item.json files, append 'Photo' to titles ending in 'snapshot', and update eBay inventory."
    )
    parser.add_argument("directory", help="Target directory (parent folder like EPSCAN_7_26, EPSCAN_7_30, or single item folder)")
    parser.add_argument("--store", default=os.environ.get("EBAY_STORE", "photo_vault"), help="eBay Store Account to use (default: EBAY_STORE or photo_vault)")
    parser.add_argument("--env", choices=["sandbox", "production"], default=os.environ.get("EBAY_ENV", "production").lower(), help="eBay environment (default: EBAY_ENV or production)")
    parser.add_argument("--count", type=int, default=None, help="Maximum number of items to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview matching items and title changes without making file modifications or eBay API calls")
    parser.add_argument("--skip-ebay", action="store_true", help="Update local JSON files only without making eBay API requests")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay in seconds between eBay API requests (default: 0.2)")

    args = parser.parse_args()

    target_dir = Path(args.directory).resolve()
    if not target_dir.exists():
        print(f"[-] Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)

    process_inventory_files(
        target_dir=target_dir,
        store=args.store,
        env=args.env,
        count=args.count,
        dry_run=args.dry_run,
        delay=args.delay,
        skip_ebay=args.skip_ebay
    )


if __name__ == "__main__":
    main()

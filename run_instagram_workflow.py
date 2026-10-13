"""Master execution script for EPSCAN Instagram Carousel Publishing Workflow.

Groups scanned photograph items into 5-photo carousels (front photos only) and publishes
them to Instagram via the official Meta Instagram Graph API.

Strict URL Validation:
  If ANY item folder in a batch lacks an image URL in its inventory_item.json,
  the program halts immediately and exits with code 1.

Usage:
  python run_instagram_workflow.py /path/to/EPSCAN/AIPH-4-L               # Process all unposted items in directory
  python run_instagram_workflow.py /path/to/EPSCAN/AIPH-4-L --dry-run    # Preview batch and caption without publishing
  python run_instagram_workflow.py folder1 folder2 folder3 folder4 folder5 # Process explicit list of 5 folders
"""

import os
import sys
import json
import argparse
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Optional

from instagram_oauth_helper import load_credentials, verify_credentials
from instagram_caption_generator import build_carousel_caption
from instagram_client import InstagramClient, InstagramGraphAPIError

DEFAULT_BATCH_SIZE = 5
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
PROJECT_ROOT = Path(__file__).resolve().parent
LOG_FILE_NAME = "instagram_log.md"


def get_front_image_url(folder_path: Path) -> Optional[str]:
    """
    Extracts the front image URL from inventory_item.json.
    Returns None if missing or empty.
    """
    inv_file = folder_path / "inventory_item.json"
    if not inv_file.exists():
        return None

    try:
        with open(inv_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        image_urls = data.get("product", {}).get("imageUrls", [])
        if image_urls and isinstance(image_urls, list) and image_urls[0]:
            return str(image_urls[0])
    except Exception:
        return None

    return None


def find_item_folders(target_path: Path) -> list[Path]:
    """
    Recursively scans target path to discover item folders containing metadata.json.
    Sorted alphabetically for deterministic grouping.
    """
    if target_path.is_file():
        target_path = target_path.parent

    # If the target itself is an item folder
    if (target_path / "metadata.json").exists():
        return [target_path]

    item_folders = []
    for meta_file in target_path.rglob("metadata.json"):
        folder = meta_file.parent
        # Skip top-level or scratch directories
        if folder.name not in ("scratch", ".venv", ".git"):
            item_folders.append(folder)

    item_folders.sort(key=lambda p: str(p))
    return item_folders


def load_item_metadata(folder_path: Path) -> Optional[dict[str, Any]]:
    """Loads metadata.json for an item folder."""
    meta_file = folder_path / "metadata.json"
    if not meta_file.exists():
        return None
    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[-] Error reading metadata in {folder_path}: {e}", file=sys.stderr)
        return None


def is_already_posted_to_instagram(meta: dict[str, Any]) -> bool:
    """
    Checks if a photo item has already been posted to Instagram.
    Returns True if an instagram_url (or legacy permalink/posted flag) exists.
    """
    if not meta or not isinstance(meta, dict):
        return False
    # Explicit check: passover if photo's metadata has an instagram URL value
    if meta.get("instagram_url") and str(meta["instagram_url"]).strip():
        return True
    if meta.get("instagram_permalink") and str(meta["instagram_permalink"]).strip():
        return True
    if meta.get("instagram_posted") is True:
        return True
    return False


def update_item_metadata_post_success(
    folder_path: Path,
    post_url: str
) -> None:
    """Updates metadata.json with the live Instagram post URL."""
    meta_file = folder_path / "metadata.json"
    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

        meta["instagram_url"] = post_url

        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=4)
    except Exception as e:
        print(f"[!] Warning: Failed to update metadata.json in {folder_path}: {e}", file=sys.stderr)


def append_to_instagram_log(
    log_dir: Path,
    batch_folders: list[Path],
    media_id: str,
    permalink: Optional[str],
    caption: str
) -> None:
    """Appends published carousel details to instagram_log.md in the folder and project root."""
    now_pacific = datetime.now(PACIFIC_TZ).strftime("%Y-%m-%d %I:%M:%S %p %Z")
    skus = [p.name for p in batch_folders]
    link_str = f"[View Post]({permalink})" if permalink else "N/A"
    row = f"| {now_pacific} | `{media_id}` | `{', '.join(skus)}` | {link_str} |\n"

    target_dirs = [log_dir]
    if PROJECT_ROOT != log_dir and PROJECT_ROOT not in target_dirs:
        target_dirs.append(PROJECT_ROOT)

    for d in target_dirs:
        log_file = d / LOG_FILE_NAME
        header_needed = not log_file.exists()
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                if header_needed:
                    f.write("# Instagram Publication Log\n\n")
                    f.write("| Timestamp | Post ID | SKUs Included | Permalink |\n")
                    f.write("| :--- | :--- | :--- | :--- |\n")
                f.write(row)
        except Exception as e:
            print(f"[!] Warning: Could not write to {log_file}: {e}", file=sys.stderr)


def process_batch(
    batch_folders: list[Path],
    client: Optional[InstagramClient],
    dry_run: bool = False,
) -> bool:
    """
    Validates and publishes a batch of photo items as a single carousel post.
    Terminates with exit code 1 if any item lacks an image URL.
    """
    print(f"\n========================================================")
    print(f"[*] Processing Batch of {len(batch_folders)} Photos:")
    for idx, f in enumerate(batch_folders, start=1):
        print(f"    [{idx}] {f.name}")
    print(f"========================================================")

    # 1. Strict Image URL Validation for ALL items in batch
    image_urls = []
    items_meta = []
    for f in batch_folders:
        meta = load_item_metadata(f)
        if not meta:
            print(f"[-] Error: Missing or corrupt metadata.json in '{f}'. Exiting.", file=sys.stderr)
            sys.exit(1)

        img_url = get_front_image_url(f)
        if not img_url:
            print(
                f"\n[-] FATAL ERROR: Item folder '{f}' has no image URL in inventory_item.json.\n"
                f"    Scanned photographs must first have an image URL (e.g. from run_ebay_workflow.py)\n"
                f"    before they can be posted to Instagram. Terminating program.\n",
                file=sys.stderr
            )
            sys.exit(1)

        image_urls.append(img_url)
        items_meta.append(meta)

    # 2. Build Multi-Item Sequential Caption
    caption = build_carousel_caption(items_meta)

    print(f"\n[+] Generated Caption ({len(caption)} / 2200 chars):\n")
    print("--------------------------------------------------------")
    print(caption)
    print("--------------------------------------------------------\n")

    if dry_run:
        print("[*] DRY RUN: Verification successful! Images validated and caption generated without posting.")
        return True

    # 3. Publish via Official Instagram Graph API
    if not client:
        print("[-] Error: Instagram client not initialized.", file=sys.stderr)
        sys.exit(1)

    try:
        now_pacific = datetime.now(PACIFIC_TZ).isoformat()
        result = client.post_carousel(image_urls=image_urls, caption=caption)
        media_id = result["media_id"]
        post_url = result.get("permalink") or f"https://www.instagram.com/p/{media_id}/"

        print(f"\n[+] Successfully published carousel to Instagram!")
        print(f"    Live Post URL: {post_url}")
        print(f"    Recording 'instagram_url' into metadata.json for all {len(batch_folders)} photos in batch...")

        # 4. Atomically update metadata.json for all items in batch with post URL
        for f in batch_folders:
            update_item_metadata_post_success(f, post_url)

        # 5. Log to instagram_log.md with current timestamp
        log_dir = batch_folders[0].parent
        append_to_instagram_log(log_dir, batch_folders, media_id, post_url, caption)

        print(f"[+] Batch successfully completed and state updated for all {len(batch_folders)} items!")
        return True
    except InstagramGraphAPIError as e:
        print(f"[-] Instagram API Error during batch publishing: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[-] Unexpected error during batch publishing: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Publish 5-photo carousels of vintage photographs to Instagram via Meta Graph API."
    )
    parser.add_argument(
        "targets",
        nargs="+",
        help="Path(s) to EPSCAN directory or individual photo item folders."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Number of front photos per carousel post (default: {DEFAULT_BATCH_SIZE})."
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Account profile name from instagram_credentials.json."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate URLs, group batches, and generate captions without publishing to Instagram."
    )
    parser.add_argument(
        "--throttle",
        type=int,
        default=30,
        help="Seconds to wait between consecutive carousel posts (default: 30)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess and post items even if marked instagram_posted=True in metadata.json."
    )

    args = parser.parse_args()

    # Collect all item folders from target arguments
    discovered_folders: list[Path] = []
    for t in args.targets:
        target_path = Path(t).resolve()
        if not target_path.exists():
            print(f"[-] Target does not exist: {target_path}", file=sys.stderr)
            sys.exit(1)
        folders = find_item_folders(target_path)
        discovered_folders.extend(folders)

    # Deduplicate while preserving order
    seen = set()
    unique_folders = []
    for f in discovered_folders:
        if str(f) not in seen:
            seen.add(str(f))
            unique_folders.append(f)

    if not unique_folders:
        print("[-] No item folders containing metadata.json found in targets.")
        sys.exit(1)

    print(f"[*] Discovered {len(unique_folders)} item folder(s).")

    # Filter out already posted items (passover if metadata has an instagram_url)
    unposted_folders = []
    passed_over_count = 0
    for f in unique_folders:
        meta = load_item_metadata(f)
        if not meta:
            continue
        if not args.force and is_already_posted_to_instagram(meta):
            passed_over_count += 1
            continue
        unposted_folders.append(f)

    if passed_over_count > 0:
        print(f"[*] Passed over {passed_over_count} item(s) (already posted to Instagram with existing URL in metadata).")

    print(f"[*] Found {len(unposted_folders)} unposted item(s) to process.")
    if not unposted_folders:
        print("[+] All discovered items have already been posted to Instagram. Nothing to do.")
        return

    # Check batch sufficiency
    batch_size = args.batch_size
    if len(unposted_folders) < batch_size and len(args.targets) > 1:
        # If user explicitly passed fewer folders than batch size, accept it
        batch_size = len(unposted_folders)
    elif len(unposted_folders) < batch_size:
        print(
            f"[-] Notice: Found {len(unposted_folders)} unposted item(s), which is fewer than "
            f"the required batch size of {batch_size}. Carousels require {batch_size} photos."
        )
        sys.exit(1)

    # Pick strictly the first available 5 photos
    selected_batch = unposted_folders[:batch_size]
    remaining_unposted = len(unposted_folders) - batch_size
    if remaining_unposted > 0:
        print(
            f"[*] Selected the first available {batch_size} unposted photos for this post "
            f"({remaining_unposted} remaining unposted item(s) will be posted in future runs)."
        )
    else:
        print(f"[*] Selected the first available {batch_size} unposted photos for this post.")

    # Initialize client if not dry-run
    client = None
    if not args.dry_run:
        creds = load_credentials(account_name=args.account)
        token = creds.get("access_token")
        account_id = creds.get("instagram_account_id")

        if not token or not account_id:
            print(
                "[-] Missing Instagram credentials. Please provide them in instagram_credentials.json\n"
                "    or via INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID environment variables.",
                file=sys.stderr
            )
            sys.exit(1)

        # Quick verify
        v = verify_credentials(token, account_id)
        if not v["success"]:
            print(f"[-] Credential verification failed: {v['error']}", file=sys.stderr)
            sys.exit(1)

        client = InstagramClient(access_token=token, instagram_account_id=account_id)

    # Process and publish the single 5-photo batch
    success = process_batch(
        batch_folders=selected_batch,
        client=client,
        dry_run=args.dry_run,
    )

    if success:
        print(f"\n[+] Workflow finished: 1 carousel post ({batch_size} photos) completed successfully.")
    else:
        print("[-] Workflow failed to publish carousel.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

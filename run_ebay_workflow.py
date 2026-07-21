import os
import sys
import json
import argparse
import io
from contextlib import redirect_stdout
from pathlib import Path

from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS
import generate_offers
import create_inventory
import create_offer
import publish_offer

def check_metadata_flag(folder_path, flag_key):
    metadata_path = folder_path / "metadata.json"
    if not metadata_path.exists():
        return False
    try:
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
        return metadata.get(flag_key, False)
    except Exception:
        return False

def execute_step(step_func, *args):
    """Executes a step function while capturing its stdout to keep logging clean."""
    f = io.StringIO()
    with redirect_stdout(f):
        try:
            step_func(*args)
        except Exception as e:
            print(f"Exception: {e}")
    return f.getvalue()

def process_single_item(folder_path, access_token, api_host):
    print(f"\n--- Processing Item: {folder_path.name} ---")
    stats = {
        "inventory_created": 0,
        "inventory_skipped": 0,
        "offer_created": 0,
        "offer_skipped": 0,
        "offer_published": 0,
        "offer_published_skipped": 0,
        "failed": False
    }
    
    # Step 0: Ensure offer.json is generated
    offer_path = folder_path / "offer.json"
    if not offer_path.exists():
        print(" -> Step 0: Generating offer.json...")
        execute_step(generate_offers.process_folder, folder_path)
    
    # Step 1: Create Inventory
    if not check_metadata_flag(folder_path, "ebay_inventory_created"):
        print(" -> Step 1: Creating Inventory Item...")
        output = execute_step(create_inventory.process_folder, folder_path, access_token, api_host)
        if not check_metadata_flag(folder_path, "ebay_inventory_created"):
            print(f" [!] Step 1 failed. Output details:\n{output}")
            stats["failed"] = True
            return stats
        else:
            print("    [+] Success")
            stats["inventory_created"] = 1
    else:
        print(" -> Step 1: Inventory already exists (Skipping).")
        stats["inventory_skipped"] = 1

    # Step 2: Create Offer
    if not check_metadata_flag(folder_path, "ebay_offer_created"):
        print(" -> Step 2: Creating Offer...")
        output = execute_step(create_offer.process_folder, folder_path, access_token, api_host)
        if not check_metadata_flag(folder_path, "ebay_offer_created"):
            print(f" [!] Step 2 failed. Output details:\n{output}")
            stats["failed"] = True
            return stats
        else:
            print("    [+] Success")
            stats["offer_created"] = 1
    else:
        print(" -> Step 2: Offer already exists (Skipping).")
        stats["offer_skipped"] = 1

    # Step 3: Publish Offer
    if not check_metadata_flag(folder_path, "ebay_offer_published"):
        print(" -> Step 3: Publishing Offer...")
        output = execute_step(publish_offer.process_folder, folder_path, access_token, api_host)
        if not check_metadata_flag(folder_path, "ebay_offer_published"):
            print(f" [!] Step 3 failed. Output details:\n{output}")
            stats["failed"] = True
            return stats
        else:
            print("    [+] Success")
            stats["offer_published"] = 1
    else:
        print(" -> Step 3: Offer already published (Skipping).")
        stats["offer_published_skipped"] = 1

    print(f" [SUCCESS] Workflow completed for {folder_path.name}!")
    return stats

def update_markdown_log(target_dir):
    """Generates a markdown log of all listed items."""
    # If target_dir is a specific item, find its parent (e.g. EPSCAN)
    scan_dir = target_dir.parent if (target_dir / "metadata.json").exists() else target_dir
    log_path = scan_dir.parent / "listing_log.md" if scan_dir.name == "EPSCAN" else scan_dir / "listing_log.md"
    
    listed_items = []
    total_items = 0
    
    for item in sorted(scan_dir.iterdir()):
        if item.is_dir() and not item.name.startswith(".") and item.name != "cheap_photos":
            total_items += 1
            if check_metadata_flag(item, "ebay_offer_published"):
                listed_items.append(item)
                
    with open(log_path, "w") as f:
        f.write("# eBay Listing Log\n\n")
        f.write(f"**Total Photos Published / Available:** {len(listed_items)} / {total_items}\n\n")
        
        if listed_items:
            f.write("## Listed Items\n\n")
            f.write("| Folder / SKU | Status | eBay Listing ID |\n")
            f.write("|--------------|--------|-----------------|\n")
            for item in listed_items:
                try:
                    with open(item / "metadata.json", "r") as m:
                        meta = json.load(m)
                        listing_id = meta.get("ebay_listing_id", "N/A")
                except Exception:
                    listing_id = "N/A"
                    
                folder_link = f"[{item.name}](file://{item.absolute()})"
                f.write(f"| {folder_link} | ✅ Published | {listing_id} |\n")
        else:
            f.write("*No items have been published yet.*\n")
            
    print(f"[*] Updated listing log at {log_path.name}")

def main():
    parser = argparse.ArgumentParser(description="Master script to run the full eBay listing workflow.")
    parser.add_argument("directory", help="Target directory (parent folder like EPSCAN, or a single product folder)")
    parser.add_argument("--env", choices=["sandbox", "production"], help="eBay environment to use (defaults to EBAY_ENV or sandbox)")
    parser.add_argument("--count", type=int, default=None, help="Maximum number of items to process")
    args = parser.parse_args()
    
    target_dir = Path(args.directory).resolve()
    if not target_dir.exists():
        print(f"[-] Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)
        
    env = args.env or os.environ.get("EBAY_ENV", "sandbox").lower()
    if env not in ENVIRONMENTS:
        env = "sandbox"
        
    api_host = ENVIRONMENTS[env]["api_host"]
    
    print("=" * 60)
    print(f"        eBay Master Listing Workflow ({env.upper()})")
    print("=" * 60)
    
    access_token = check_and_get_token(env=env)
    if not access_token:
        print("[-] Error: Could not get a valid access token. Please run 'python ebay_oauth_helper.py' to log in.")
        sys.exit(1)

    raw_items = []
    
    # Determine if target_dir is a single item or a parent directory
    if (target_dir / "metadata.json").exists() and target_dir.name != "cheap_photos":
        raw_items.append(target_dir)
    else:
        for item in sorted(target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith(".") and item.name != "cheap_photos":
                raw_items.append(item)

    items_to_process = []
    for item in raw_items:
        # Only include items that have not yet been fully published
        if not check_metadata_flag(item, "ebay_offer_published"):
            items_to_process.append(item)
                
    if not items_to_process:
        print(f"[-] No unprocessed product folders found in {target_dir}")
        sys.exit(0)

    # Apply limit if specified
    if args.count is not None and args.count > 0:
        items_to_process = items_to_process[:args.count]
        print(f"[*] Limiting execution to {args.count} unprocessed item(s) as requested.")
        
    print(f"[*] Found {len(items_to_process)} unprocessed item(s) to process.")

    total_stats = {
        "inventory_created": 0,
        "inventory_skipped": 0,
        "offer_created": 0,
        "offer_skipped": 0,
        "offer_published": 0,
        "offer_published_skipped": 0,
        "failed_items": 0
    }

    for item_dir in items_to_process:
        stats = process_single_item(item_dir, access_token, api_host)
        if stats["failed"]:
            total_stats["failed_items"] += 1
        for key in ["inventory_created", "inventory_skipped", "offer_created", "offer_skipped", "offer_published", "offer_published_skipped"]:
            total_stats[key] += stats[key]
            
    print("\n" + "=" * 60)
    print(f"Workflow Complete! Processed {len(items_to_process)} items.")
    print("-" * 60)
    print("STATS:")
    print(f"  - Inventories Created: {total_stats['inventory_created']} (Skipped: {total_stats['inventory_skipped']})")
    print(f"  - Offers Created:      {total_stats['offer_created']} (Skipped: {total_stats['offer_skipped']})")
    print(f"  - Offers Published:    {total_stats['offer_published']} (Skipped: {total_stats['offer_published_skipped']})")
    print(f"  - Failed Items:        {total_stats['failed_items']}")
    print("=" * 60)

    # Update the markdown log after the workflow finishes
    update_markdown_log(target_dir)

if __name__ == "__main__":
    main()

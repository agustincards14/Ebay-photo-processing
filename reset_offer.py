#!/usr/bin/env python3
import os
import sys
import json
import argparse
import requests
from pathlib import Path
from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS
import create_inventory

def delete_offer_api(offer_id, access_token, api_host):
    """Deletes an offer from eBay by offerId."""
    print(f"[*] Deleting offer '{offer_id}' from eBay...")
    url = f"{api_host}/sell/inventory/v1/offer/{offer_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Language": "en-US",
    }
    try:
        response = requests.delete(url, headers=headers)
        if response.status_code in (200, 204):
            print(f"[+] Successfully deleted offer '{offer_id}'.")
            return True
        elif response.status_code == 404:
            print(f"[*] Offer '{offer_id}' not found on eBay (404).")
            return True
        else:
            print(f"[-] Failed to delete offer '{offer_id}'. Status code: {response.status_code}")
            print(f"    Details: {response.text}")
            return False
    except Exception as e:
        print(f"[-] Exception deleting offer '{offer_id}': {e}")
        return False

def delete_inventory_item_api(sku, access_token, api_host):
    """Deletes an inventory item from eBay by SKU."""
    print(f"[*] Deleting inventory item SKU '{sku}' from eBay...")
    url = f"{api_host}/sell/inventory/v1/inventory_item/{sku}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Language": "en-US",
    }
    try:
        response = requests.delete(url, headers=headers)
        if response.status_code in (200, 204):
            print(f"[+] Successfully deleted inventory item SKU '{sku}'.")
            return True
        elif response.status_code == 404:
            print(f"[*] Inventory item SKU '{sku}' not found on eBay (404).")
            return True
        else:
            print(f"[-] Failed to delete inventory item SKU '{sku}'. Status code: {response.status_code}")
            print(f"    Details: {response.text}")
            return False
    except Exception as e:
        print(f"[-] Exception deleting inventory item SKU '{sku}': {e}")
        return False

def reset_and_reupload_folder(folder_path, env="production"):
    """
    Deletes offer and inventory item on eBay, resets local metadata flags,
    and re-uploads/re-creates the inventory item on eBay.
    """
    metadata_path = folder_path / "metadata.json"
    if not metadata_path.exists():
        print(f"[-] Error: '{metadata_path}' does not exist.", file=sys.stderr)
        return False

    with open(metadata_path, "r") as f:
        metadata = json.load(f)

    access_token = check_and_get_token(env=env)
    if not access_token:
        print(f"[-] Error: Could not obtain valid OAuth token for {env.upper()}.", file=sys.stderr)
        return False

    api_host = ENVIRONMENTS[env]["api_host"]

    # 1. Delete offer on eBay if offer_id exists
    offer_id = metadata.get("ebay_offer_id")
    if offer_id:
        delete_offer_api(offer_id, access_token, api_host)
    else:
        print(f"[*] No 'ebay_offer_id' found in {folder_path.name}/metadata.json.")

    # 2. Delete inventory item on eBay
    sku = metadata.get("sku", folder_path.name)
    delete_inventory_item_api(sku, access_token, api_host)

    # 3. Reset metadata fields
    metadata["ebay_inventory_created"] = False
    metadata["ebay_offer_created"] = False
    metadata["ebay_offer_published"] = False
    if "ebay_offer_id" in metadata:
        del metadata["ebay_offer_id"]
    if "ebay_listing_id" in metadata:
        del metadata["ebay_listing_id"]

    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=4)
    print(f"[+] Reset metadata flags for '{folder_path.name}'.")

    # 4. Re-upload inventory item to eBay
    print(f"[*] Re-uploading inventory item for '{folder_path.name}'...")
    create_inventory.process_folder(folder_path, access_token, api_host)

    print(f"\n[SUCCESS] Completed reset & inventory re-upload for '{folder_path.name}'.")
    return True

def main():
    parser = argparse.ArgumentParser(
        description="Reset offer & inventory state for a folder (deletes offer + inventory on eBay, resets metadata, and re-uploads inventory)."
    )
    parser.add_argument("directory", nargs="?", help="Target product folder (e.g., EPSCAN/220121_0341)")
    parser.add_argument("--env", choices=["sandbox", "production"], help="eBay environment to use (defaults to EBAY_ENV or production)")

    args = parser.parse_args()

    if not args.directory:
        print("[-] Error: Product directory argument is required.", file=sys.stderr)
        print("Usage: python reset_offer.py <folder> [--env production|sandbox]", file=sys.stderr)
        sys.exit(1)

    target_dir = Path(args.directory).resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        print(f"[-] Error: Target directory '{target_dir}' does not exist or is not a directory.", file=sys.stderr)
        sys.exit(1)

    env = args.env or os.environ.get("EBAY_ENV", "production").lower()
    if env not in ENVIRONMENTS:
        env = "production"

    print("=" * 60)
    print(f"     eBay Offer & Inventory Reset Tool ({env.upper()})")
    print("=" * 60)

    success = reset_and_reupload_folder(target_dir, env=env)
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()

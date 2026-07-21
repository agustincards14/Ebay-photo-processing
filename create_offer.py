import os
import sys
import json
import argparse
from pathlib import Path
import requests
from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS

def process_folder(folder_path, access_token, api_host):
    """Creates an offer for an existing inventory item based on offer.json and metadata.json."""
    metadata_path = folder_path / "metadata.json"
    offer_path = folder_path / "offer.json"
    
    if not metadata_path.exists():
        print(f"[-] Skipping {folder_path.name}: No metadata.json found.")
        return

    if not offer_path.exists():
        print(f"[-] Skipping {folder_path.name}: No offer.json found.")
        return

    with open(metadata_path, "r") as f:
        metadata = json.load(f)

    # Check if offer is already created
    if metadata.get("ebay_offer_created"):
        print(f"[*] Skipping {folder_path.name}: Offer already created (offerId: {metadata.get('ebay_offer_id')}).")
        return
        
    # We must have an inventory item created first!
    if not metadata.get("ebay_inventory_created"):
        print(f"[-] Skipping {folder_path.name}: Inventory item not created yet. Run create_inventory.py first.")
        return

    sku = metadata.get("sku", folder_path.name)
    
    # Load the pre-generated offer payload
    with open(offer_path, "r") as f:
        offer_payload = json.load(f)

    print(f"\n[*] Processing folder: {folder_path.name} (SKU: {sku})")

    url = f"{api_host}/sell/inventory/v1/offer"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Language": "en-US",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, headers=headers, json=offer_payload)
        
        # 201 Created is returned for successful POST requests
        if response.status_code == 201:
            offer_id = response.json().get("offerId")
            print(f"[+] Successfully created offer for SKU {sku}. Offer ID: {offer_id}")
            
            # Update metadata.json
            metadata["ebay_offer_created"] = True
            metadata["ebay_offer_id"] = offer_id
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=4)
            print(f"[+] Updated {metadata_path.name} with ebay_offer_created=true and offerId.")
        else:
            print(f"[-] Failed to create offer for SKU {sku}. Status code: {response.status_code}")
            print(f"    Details: {response.text}")
    except Exception as e:
        print(f"[-] Exception creating offer for {folder_path.name}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Create offers on eBay using local offer.json files.")
    parser.add_argument("directory", nargs="?", default=os.getcwd(), help="Target directory (defaults to CWD)")
    parser.add_argument("--env", choices=["sandbox", "production"], help="eBay environment to use (defaults to EBAY_ENV or sandbox)")
    args = parser.parse_args()
    
    target_dir = Path(args.directory).resolve()
    if not target_dir.exists():
        print(f"[-] Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)
        
    env = args.env or os.environ.get("EBAY_ENV", "sandbox").lower()
    if env not in ENVIRONMENTS:
        env = "sandbox"
        
    api_host = ENVIRONMENTS[env]["api_host"]
    env_label = env.upper()
    
    print("=" * 60)
    print(f"           eBay Offer Creator ({env_label})")
    print("=" * 60)
    
    access_token = check_and_get_token(env=env)
    if not access_token:
        print(f"[-] Error: Could not get a valid access token for {env_label}.")
        print("    Please run 'python ebay_oauth_helper.py --env <env>' to log in.")
        sys.exit(1)

    if (target_dir / "metadata.json").exists():
        process_folder(target_dir, access_token, api_host)
    else:
        folders_processed = 0
        for item in sorted(target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                process_folder(item, access_token, api_host)
                folders_processed += 1
        if folders_processed == 0:
            print(f"[-] No valid subdirectories found in {target_dir}")

if __name__ == "__main__":
    main()

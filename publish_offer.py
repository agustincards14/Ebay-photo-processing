import os
import sys
import json
import argparse
from pathlib import Path
import requests
from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS

def process_folder(folder_path, access_token, api_host):
    """Publishes an offer on eBay and updates metadata.json with the listingId."""
    metadata_path = folder_path / "metadata.json"
    
    if not metadata_path.exists():
        print(f"[-] Skipping {folder_path.name}: No metadata.json found.")
        return

    with open(metadata_path, "r") as f:
        metadata = json.load(f)

    # Check if offer is already published
    if metadata.get("ebay_offer_published"):
        print(f"[*] Skipping {folder_path.name}: Offer already published (listingId: {metadata.get('ebay_listing_id')}).")
        return
        
    offer_id = metadata.get("ebay_offer_id")
    if not offer_id:
        print(f"[-] Skipping {folder_path.name}: No 'ebay_offer_id' found. Has the offer been created?")
        return

    print(f"\n[*] Processing folder: {folder_path.name} (Offer ID: {offer_id})")

    # API call to POST publish offer
    url = f"{api_host}/sell/inventory/v1/offer/{offer_id}/publish"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Language": "en-US",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, headers=headers)
        
        # 200 OK is returned for successful publish requests
        if response.status_code == 200:
            res_data = response.json()
            listing_id = res_data.get("listingId")
            print(f"[+] Successfully published offer {offer_id}. Listing ID: {listing_id}")
            
            # Update metadata.json
            metadata["ebay_offer_published"] = True
            metadata["ebay_listing_id"] = listing_id
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=4)
            print(f"[+] Updated {metadata_path.name} with ebay_offer_published=true and ebay_listing_id.")
        else:
            print(f"[-] Failed to publish offer {offer_id}. Status code: {response.status_code}")
            print(f"    Details: {response.text}")
    except Exception as e:
        print(f"[-] Exception publishing offer for {folder_path.name}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Publish offers on eBay using local metadata.json.")
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
    print(f"           eBay Offer Publisher ({env_label})")
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

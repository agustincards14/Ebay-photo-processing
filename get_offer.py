import os
import sys
import json
import argparse
from pathlib import Path
import requests
from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS

def get_offer(folder_path, access_token, api_host):
    """Gets an offer from eBay for a given folder's offerId."""
    metadata_path = folder_path / "metadata.json"
    
    if not metadata_path.exists():
        print(f"[-] Error: No metadata.json found in '{folder_path.name}'.")
        return

    with open(metadata_path, "r") as f:
        metadata = json.load(f)

    offer_id = metadata.get("ebay_offer_id")
    if not offer_id:
        print(f"[-] Error: No 'ebay_offer_id' found in metadata.json for {folder_path.name}. Has the offer been created?")
        return

    print(f"\n[*] Fetching offer for Offer ID: {offer_id}")

    # API call to GET offer
    url = f"{api_host}/sell/inventory/v1/offer/{offer_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Language": "en-US",
    }

    try:
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            print(f"[+] Successfully fetched offer for Offer ID {offer_id}:")
            print(json.dumps(response.json(), indent=4))
        elif response.status_code == 404:
            print(f"[-] Offer with ID {offer_id} not found (404).")
        else:
            print(f"[-] Failed to get offer {offer_id}. Status code: {response.status_code}")
            print(f"    Details: {response.text}")
    except Exception as e:
        print(f"[-] Exception getting offer {offer_id}: {e}")

def main():
    parser = argparse.ArgumentParser(description="GET an offer from eBay using the ebay_offer_id in a folder's metadata.json.")
    parser.add_argument("directory", help="Path to the folder containing metadata.json")
    parser.add_argument("--env", choices=["sandbox", "production"], help="eBay environment to use (defaults to EBAY_ENV or sandbox)")
    args = parser.parse_args()
    
    # Resolve target directory
    target_dir = Path(args.directory).resolve()
    if not target_dir.exists():
        print(f"[-] Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)
        
    if not target_dir.is_dir():
        print(f"[-] Error: Target '{target_dir}' is not a directory.")
        sys.exit(1)
        
    # Resolve environment
    env = args.env or os.environ.get("EBAY_ENV", "sandbox").lower()
    if env not in ENVIRONMENTS:
        env = "sandbox"
        
    api_host = ENVIRONMENTS[env]["api_host"]
    env_label = env.upper()
    
    print("=" * 60)
    print(f"          eBay Offer Fetcher ({env_label})")
    print("=" * 60)
    
    # Retrieve access token
    access_token = check_and_get_token(env=env)
    if not access_token:
        print(f"[-] Error: Could not get a valid access token for {env_label}.")
        print("    Please run 'python ebay_oauth_helper.py --env <env>' to log in.")
        sys.exit(1)
        
    get_offer(target_dir, access_token, api_host)

if __name__ == "__main__":
    main()

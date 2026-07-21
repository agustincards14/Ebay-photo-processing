import os
import sys
import json
import argparse
from pathlib import Path
import requests
from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS

def process_folder(folder_path, access_token, api_host):
    """Processes a single photo folder to create an inventory item on eBay."""
    metadata_path = folder_path / "metadata.json"
    item_json_path = folder_path / "inventory_item.json"
    
    if not metadata_path.exists():
        print(f"[-] Skipping {folder_path.name}: No metadata.json found.")
        return
        
    if not item_json_path.exists():
        print(f"[-] Skipping {folder_path.name}: No inventory_item.json found.")
        return

    with open(metadata_path, "r") as f:
        metadata = json.load(f)

    # Check if already created
    if metadata.get("ebay_inventory_created"):
        print(f"[*] Skipping {folder_path.name}: Already created on eBay (ebay_inventory_created=true).")
        return

    sku = metadata.get("sku")
    if not sku:
        # Fallback to directory name if not in metadata
        sku = folder_path.name
        print(f"[*] No 'sku' found in metadata.json for {folder_path.name}. Using folder name '{sku}' as SKU.")

    with open(item_json_path, "r") as f:
        item_data = json.load(f)

    print(f"\n[*] Processing folder: {folder_path.name} (SKU: {sku})")

    # API call to PUT inventory item
    url = f"{api_host}/sell/inventory/v1/inventory_item/{sku}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Language": "en-US",
        "Content-Type": "application/json"
    }

    try:
        response = requests.put(url, headers=headers, json=item_data)
        
        # 204 No Content is returned for successful PUT requests
        if response.status_code in (200, 201, 204):
            print(f"[+] Successfully created inventory item for SKU {sku}.")
            
            # Update metadata.json
            metadata["ebay_inventory_created"] = True
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=4)
            print(f"[+] Updated {metadata_path.name} with ebay_inventory_created=true.")
        else:
            print(f"[-] Failed to create inventory item for SKU {sku}. Status code: {response.status_code}")
            print(f"    Details: {response.text}")
    except Exception as e:
        print(f"[-] Exception creating inventory item for {folder_path.name}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Create inventory items on eBay from local folders.")
    parser.add_argument("directory", nargs="?", default=os.getcwd(), help="Target directory (defaults to CWD)")
    parser.add_argument("--env", choices=["sandbox", "production"], help="eBay environment to use (defaults to EBAY_ENV or sandbox)")
    args = parser.parse_args()
    
    # Resolve target directory
    target_dir = Path(args.directory).resolve()
    if not target_dir.exists():
        print(f"[-] Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)
        
    # Resolve environment
    env = args.env or os.environ.get("EBAY_ENV", "sandbox").lower()
    if env not in ENVIRONMENTS:
        env = "sandbox"
        
    api_host = ENVIRONMENTS[env]["api_host"]
    env_label = env.upper()
    
    print("=" * 60)
    print(f"         eBay Inventory Item Creator ({env_label})")
    print("=" * 60)
    
    # Retrieve access token
    access_token = check_and_get_token(env=env)
    if not access_token:
        print(f"[-] Error: Could not get a valid access token for {env_label}.")
        print("    Please run 'python ebay_oauth_helper.py --env <env>' to log in.")
        sys.exit(1)
        
    print(f"[+] Access token successfully retrieved for {env_label}.")
    
    # Check if target_dir itself contains metadata.json and inventory_item.json
    if (target_dir / "metadata.json").exists() and (target_dir / "inventory_item.json").exists():
        process_folder(target_dir, access_token, api_host)
    else:
        # Scan subdirectories
        folders_processed = 0
        for item in sorted(target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                process_folder(item, access_token, api_host)
                folders_processed += 1
        if folders_processed == 0:
            print(f"[-] No valid subdirectories found in {target_dir}")

if __name__ == "__main__":
    main()

import os
import sys
import json
import argparse
import re
from pathlib import Path
import requests
from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS

def upload_image(file_path, access_token, api_host):
    """Uploads a single image to eBay Picture Services using the Media API.
    
    Returns the uploaded image URL, or None if failed.
    """
    upload_url = f"{api_host}/commerce/media/v1_beta/image/create_image_from_file"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    
    print(f"[*] Uploading {file_path.name} to eBay Media API...")
    
    try:
        with open(file_path, "rb") as f:
            files = {
                "image": (file_path.name, f, "image/jpeg")
            }
            # requests sets the multipart Content-Type header with the boundary automatically
            response = requests.post(upload_url, headers=headers, files=files)
            
        if response.status_code == 201:
            data = response.json()
            ebay_url = data.get("maxDimensionImageUrl") or data.get("imageUrl")
            if ebay_url:
                print(f"[+] Upload success: {file_path.name} -> {ebay_url}")
                return ebay_url
            
            # Fallback: parse Location header if imageUrl not directly in response body
            location = response.headers.get("Location")
            if location:
                # Format: https://apim.ebay.com/commerce/media/v1_beta/image/{image_id}
                match = re.search(r"/image/([^/]+)", location)
                if match:
                    image_id = match.group(1)
                    print(f"[*] Fetching uploaded image details for ID: {image_id}...")
                    get_url = f"{api_host}/commerce/media/v1_beta/image/{image_id}"
                    get_resp = requests.get(get_url, headers=headers)
                    if get_resp.status_code == 200:
                        get_data = get_resp.json()
                        ebay_url = get_data.get("maxDimensionImageUrl") or get_data.get("imageUrl")
                        if ebay_url:
                            print(f"[+] Upload success: {file_path.name} -> {ebay_url}")
                            return ebay_url
            
            print(f"[-] Warning: Uploaded successfully but could not extract image URL for {file_path.name}.")
            return None
        else:
            print(f"[-] Failed to upload {file_path.name}. Status code: {response.status_code}")
            print(f"    Details: {response.text}")
            return None
            
    except Exception as e:
        print(f"[-] Exception uploading {file_path.name}: {e}")
        return None

def process_folder(folder_path, access_token, api_host):
    """Processes a single photo folder, uploads its images, and updates its inventory_item.json."""
    item_json_path = folder_path / "inventory_item.json"
    if not item_json_path.exists():
        print(f"[-] Skipping {folder_path.name}: No inventory_item.json found.")
        return
        
    print(f"\n[*] Processing folder: {folder_path.name}")
    
    with open(item_json_path, "r") as f:
        item_data = json.load(f)
        
    image_files = sorted([
        f for f in folder_path.iterdir()
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg")
    ])
    
    if not image_files:
        print(f"[-] Skipping {folder_path.name}: No JPG/JPEG images found to upload.")
        return
        
    new_image_urls = []
    for img_file in image_files:
        ebay_url = upload_image(img_file, access_token, api_host)
        if ebay_url:
            new_image_urls.append(ebay_url)
            
    if new_image_urls:
        if "product" not in item_data:
            item_data["product"] = {}
        item_data["product"]["imageUrls"] = new_image_urls
        
        with open(item_json_path, "w") as f:
            json.dump(item_data, f, indent=4)
        print(f"[+] Successfully updated imageUrls in {item_json_path.name}")
    else:
        print(f"[-] Warning: No images were successfully uploaded for {folder_path.name}. inventory_item.json was not updated.")

def main():
    parser = argparse.ArgumentParser(description="Upload images to eBay and update local inventory_item.json files.")
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
    media_api_host = "https://apim.ebay.com" if env == "production" else api_host
    env_label = env.upper()
    
    print("=" * 60)
    print(f"           eBay Image Uploader ({env_label})")
    print("=" * 60)
    
    # Retrieve access token
    access_token = check_and_get_token(env=env)
    if not access_token:
        print(f"[-] Error: Could not get a valid access token for {env_label}.")
        print("    Please run 'python ebay_oauth_helper.py --env <env>' to log in.")
        sys.exit(1)
        
    print(f"[+] Access token successfully retrieved for {env_label}.")
    
    # Check if target_dir itself contains inventory_item.json
    if (target_dir / "inventory_item.json").exists():
        process_folder(target_dir, access_token, media_api_host)
    else:
        # Scan subdirectories
        folders_processed = 0
        for item in sorted(target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                process_folder(item, access_token, media_api_host)
                folders_processed += 1
        if folders_processed == 0:
            print(f"[-] No subdirectories found in {target_dir}")

if __name__ == "__main__":
    main()

import os
import json
from pathlib import Path
import argparse

def process_folder(folder_path):
    metadata_path = folder_path / "metadata.json"
    
    if not metadata_path.exists():
        return

    with open(metadata_path, "r") as f:
        metadata = json.load(f)

    sku = metadata.get("sku", folder_path.name)
    price_str = metadata.get("price", "0")
    clean_price = price_str.replace('$', '').replace(',', '').strip()
    try:
        price_val = f"{float(clean_price):.2f}"
    except ValueError:
        price_val = "0.00"

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
        "listingPolicies": {
            "fulfillmentPolicyId": "251347744026",
            "returnPolicyId": "251347661026",
            "paymentPolicyId": "251347757026"
        }
    }

    offer_path = folder_path / "offer.json"
    with open(offer_path, "w") as f:
        json.dump(offer_data, f, indent=4)
    print(f"[+] Generated offer.json for {folder_path.name}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", help="Target directory (e.g. EPSCAN)")
    args = parser.parse_args()
    
    target_dir = Path(args.directory).resolve()
    if not target_dir.exists():
        print("Directory not found")
        return

    if (target_dir / "metadata.json").exists():
        process_folder(target_dir)
    else:
        for item in sorted(target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                process_folder(item)

if __name__ == "__main__":
    main()

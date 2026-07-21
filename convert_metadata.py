"""Convert generated photo metadata into eBay inventory item payloads.

This script reads metadata.json files created for scanned photograph folders and
transforms them into inventory_item.json files shaped for the eBay Sell
Inventory API. It derives orientation from the stored photo size, collects the
folder's JPG or JPEG filenames into placeholder image URLs, maps extracted
metadata into eBay product aspects, and writes one inventory item payload per
photo folder.

Requirements:
- Each target folder must contain a metadata.json file produced by the photo
    processing workflow.
- Image files should already be organized inside each photo folder.

Usage:
- python convert_metadata.py /path/to/photo-folder-or-parent-directory
- If no directory is provided, the script defaults to the current working
    directory.
"""

import os
import json
import re
import argparse
from pathlib import Path


def process_folder(folder_path):
    metadata_path = folder_path / "metadata.json"
    if not metadata_path.exists():
        return

    print(f"[*] Found metadata.json in {folder_path.name}")

    with open(metadata_path, "r") as f:
        meta = json.load(f)

    # Get JPG/JPEG files in the subfolder
    image_files = sorted(
        [
            f.name
            for f in folder_path.iterdir()
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg")
        ]
    )

    # Determine Orientation from size
    # Example size format: "3.7x4.8in"
    size_str = meta.get("size", "4x6in")
    orientation = "Portrait"
    match = re.match(r"([\d\.]+)\s*x\s*([\d\.]+)", size_str.lower())
    if match:
        width = float(match.group(1))
        height = float(match.group(2))
        if width > height:
            orientation = "Landscape"
        elif width == height:
            orientation = "Square"
        else:
            orientation = "Portrait"

    # Save to inventory_item.json
    output_path = folder_path / "inventory_item.json"

    # Check if inventory_item.json already exists to preserve existing imageUrls
    existing_image_urls = None
    if output_path.exists():
        try:
            with open(output_path, "r") as f:
                existing_data = json.load(f)
                existing_image_urls = existing_data.get("product", {}).get("imageUrls")
        except Exception as e:
            print(
                f"[*] Warning: Could not read existing {output_path.name} to pull imageUrls: {e}"
            )

    # Build imageUrls (placeholders based on filenames, or use existing from file if found)
    if existing_image_urls:
        image_urls = existing_image_urls
    else:
        image_urls = [
            f"https://example.com/scans/{folder_path.name}/{img_name}"
            for img_name in image_files
        ]

    # Build aspects
    aspects = {
        "Subject": meta.get("subjects", []),
        "Year of Production": [meta.get("year", "Unknown")],
        "Unit of Sale": ["Single Piece"],
        "Size": [size_str],
        "Vintage": ["Yes"],
        "Antique": ["No"],
        "Signed": ["No"],
        "Material": ["Paper"],
        "Unit of Sale": ["Single Piece"],
        "Production Technique": ["Gelatin-Silver Print"],
        "Country of Origin": ["United States"],
        "Number of Photographs": ["1"],
        "Image Orientation": [orientation],
        "Image Color": ["Black & White"],
        "Type": ["Photograph"],
        "Original/Licensed Reprint": ["Original"],
        "Style": ["Photojournalism"],
        "Features": ["One of a Kind (OOAK)"],
        "Framing": ["Unframed"],
        "Production Technique": ["Photocopy"],
        "Size Type/Largest Dimension": ['Small (Up to 7")'],
        "Listed By": ["Dealer or Reseller"],
        "Year": [meta.get("year", "Unknown")],
    }

    if "location" in meta:
        aspects["Location"] = [meta["location"]]

    if "theme" in meta:
        aspects["Theme"] = (
            meta["theme"] if isinstance(meta["theme"], list) else [meta["theme"]]
        )

    # Build the Inventory Item payload
    inventory_item = {
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "condition": "USED_EXCELLENT",
        "conditionDescription": f"Vintage photograph. Year: {meta.get('year')}. Size: {size_str}.",
        "packageWeightAndSize": {
            "dimensions": {
                "length": 7.0,
                "width": 5.0,
                "height": 0.125,
                "unit": "INCH",
            },
            "packageType": "LETTER",
            "weight": {
                "value": 1.0,
                "unit": "OUNCE",
            },
        },
        "product": {
            "title": meta.get("title", "")[:80],  # Keep title under 80 chars
            "description": meta.get("description", ""),
            "aspects": aspects,
            "imageUrls": image_urls,
        },
    }

    # Save to inventory_item.json
    with open(output_path, "w") as f:
        json.dump(inventory_item, f, indent=4)

    print(f"[+] Created/Updated: {output_path.resolve()}")


def convert_all(directory_path):
    base_dir = Path(directory_path)

    if not base_dir.exists():
        print(f"[-] Error: Directory {base_dir} does not exist.")
        return

    # Check if the directory itself is a photo folder containing metadata.json
    if (base_dir / "metadata.json").exists():
        process_folder(base_dir)
    else:
        # Otherwise, process subfolders
        processed_any = False
        for subfolder in base_dir.iterdir():
            if subfolder.is_dir() and (subfolder / "metadata.json").exists():
                process_folder(subfolder)
                processed_any = True
        if not processed_any:
            print(
                f"[-] No metadata.json files found directly or in subfolders of {base_dir.resolve()}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert metadata.json files to eBay inventory_item.json files."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=os.getcwd(),
        help="Directory to run the conversion on (defaults to CWD)",
    )
    args = parser.parse_args()

    convert_all(args.directory)

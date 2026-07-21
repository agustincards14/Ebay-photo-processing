"""Migrate theme/themes from metadata.json to Theme in inventory_item.json aspects.

This script scans a directory (or its subdirectories) for metadata.json files.
If a metadata.json contains a 'themes' field, the script:
1. Copies those themes as a list of strings into product.aspects.Theme in the
   corresponding inventory_item.json file (if it exists).
2. Deletes 'theme' and 'themes' fields from the metadata.json file.

Usage:
- python migrate_themes.py [/path/to/directory]
- If no directory is provided, it defaults to the EPSCAN directory in the script's folder.
"""

import sys
import json
import argparse
from pathlib import Path

def migrate_folder(folder_path: Path):
    metadata_path = folder_path / "metadata.json"
    item_path = folder_path / "inventory_item.json"
    
    if not metadata_path.exists():
        return
        
    try:
        # 1. Load metadata
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
            
        # Check if 'themes' or 'theme' exists
        has_themes = "themes" in metadata
        has_theme = "theme" in metadata
        
        if not (has_themes or has_theme):
            return
            
        print(f"[*] Found themes in metadata for {folder_path.name}")
        
        # 2. Extract themes
        themes_list = []
        if has_themes:
            themes_val = metadata["themes"]
            if isinstance(themes_val, list):
                themes_list = [str(t) for t in themes_val]
            elif isinstance(themes_val, str):
                themes_list = [themes_val]
        elif has_theme:
            theme_val = metadata["theme"]
            if isinstance(theme_val, str):
                themes_list = [theme_val]
                
        # 3. Copy to inventory_item.json if it exists
        if item_path.exists():
            with open(item_path, "r") as f:
                item_data = json.load(f)
                
            # Ensure path product -> aspects exists
            if "product" not in item_data:
                item_data["product"] = {}
            if "aspects" not in item_data["product"]:
                item_data["product"]["aspects"] = {}
                
            item_data["product"]["aspects"]["Theme"] = themes_list
            
            with open(item_path, "w") as f:
                json.dump(item_data, f, indent=4)
            print(f"  [+] Copied Theme {themes_list} to inventory_item.json")
        else:
            print(f"  [!] Warning: inventory_item.json does not exist in {folder_path.name}")
            
        # 4. Remove theme & themes from metadata.json
        if "theme" in metadata:
            del metadata["theme"]
        if "themes" in metadata:
            del metadata["themes"]
            
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=4)
        print(f"  [+] Removed theme/themes from metadata.json")
        
    except Exception as e:
        print(f"  [-] Error processing folder {folder_path.name}: {e}")

def migrate_all(directory_path: str):
    dir_path = Path(directory_path).resolve()
    if not dir_path.exists():
        print(f"Error: Directory '{directory_path}' does not exist.")
        sys.exit(1)
        
    # Check if the directory itself is a photo folder containing metadata.json
    if (dir_path / "metadata.json").exists():
        migrate_folder(dir_path)
    else:
        # Otherwise, process subfolders
        subfolders = sorted([f for f in dir_path.iterdir() if f.is_dir() and not f.name.startswith(".")])
        print(f"Scanning {len(subfolders)} subfolders in '{dir_path}'...")
        for subfolder in subfolders:
            migrate_folder(subfolder)
            
    print("[*] Migration completed.")

if __name__ == "__main__":
    script_dir = Path(__file__).parent.resolve()
    parser = argparse.ArgumentParser(description="Migrate themes from metadata.json to inventory_item.json.")
    parser.add_argument(
        "directory",
        nargs="?",
        default=str(script_dir / "EPSCAN"),
        help="Directory containing the photo folders (defaults to EPSCAN)",
    )
    args = parser.parse_args()
    
    migrate_all(args.directory)

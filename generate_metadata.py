"""Generate metadata for a pair of scanned photos using Gemini API.

This script takes a folder containing front and back images of a photograph,
identifies which is the front and back, calculates its physical size, and sends
both images to the Gemini API with structured JSON output schema to generate metadata.
The metadata is saved as metadata.json in the same folder.

Requirements:
- The GEMINI_API_KEY environment variable must be set.

Usage:
- python generate_metadata.py /path/to/item-folder
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field
# pyrefly: ignore [missing-import]
from google import genai
# pyrefly: ignore [missing-import]
from google.genai import types
# pyrefly: ignore [missing-import]
from PIL import Image

# Global client initialized lazily
_client = None

def get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Error: GEMINI_API_KEY environment variable is not set.")
            sys.exit(1)
        _client = genai.Client(api_key=api_key)
    return _client

# Official eBay themes for category 262421
EBAY_THEMES = [
    "Advertising", "Agriculture", "Americana", "Amusement Parks", "Animals", "Animation", 
    "Anime & Manga", "Architecture", "Art", "Astrology", "Automobilia", "Aviation", 
    "Celebrities", "Cities & Towns", "Comics", "Community Life", "Conflicts & Wars", 
    "Continents & Countries", "Cultures & Ethnicities", "Cycling", "Dance", "Disasters", 
    "Domestic & Family Life", "Events & Festivals", "Exhibitions", "Fairy Tales", 
    "Famous Places", "Fantasy", "Fashion", "Fire Department", "Floral", "Food & Drink", 
    "Glamor", "Graffiti", "History", "Hobbies & Leisure", "Holidays", "Industrial", 
    "Inspirational", "Judaica", "Language", "Law Enforcement", "Leisure", "Love", 
    "Maritime", "Militaria", "Movies", "Music", "Mythological", "Natural History", 
    "Nature", "Nautical", "Patriotic", "People", "Politics", "Portrait", "Railroadiana", 
    "Religious", "Risqué", "Royalty", "Science & Medicine", "Social History", "Sports", 
    "Stamps", "Technology", "Television", "Theater", "Topographical", "Transportation", 
    "Travel", "Travel & Transportation", "Universities", "Video Games", "Western", 
    "Working Life"
]

def build_ebay_safe_sku(folder_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "", folder_name)[:50]

DEFAULT_PRICE = "$19.95"

class PhotoMetadata(BaseModel):
    title: str = Field(
        description=(
            "Descriptive title of the photo strictly under 80 characters. "
            "Must always include the word 'Photo' (e.g., '... Snapshot Photo' or '... Vintage Photo'). "
            "Incorporate rich subject-descriptive adjectives (e.g., age, size, gender, color, style/attire, mood, relationship) "
            "and environment/setting details (e.g., climate, color, structure, ambience). "
            "Prioritize the main subject with rich adjectives, then append key context (year/decade, location, format). "
            "Example: 'Smiling Young Couple on Sunny Beach, 1952 Florida B&W Snapshot Photo'. Strictly under 80 characters."
        )
    )
    year: str = Field(description="Exact year the photo was taken (estimated if unknown).")
    subjects: list[str] = Field(description="Subjects describing the photo.")
    themes: list[str] = Field(description="List of 1 to 3 relevant themes selected from the allowed list, ordered by relevance.")
    location: str = Field(description="Location where the photo was taken (estimated or general location/city).")
    image_color: str = Field(description="Color style: 'Black & White', 'Sepia', 'Color', 'Cyanotype', or 'Monochrome'.")
    photo_type: str = Field(description="Physical format: 'Snapshot', 'Real Photo Postcard (RPPC)', 'Cabinet Card', 'Photograph', 'Stereoview', 'Tintype', 'Slide'.")
    back_text_transcription: str = Field(description="Verbatim transcription of any handwritten notes, stamps, typewriter text, or annotations on the back. Leave empty string if none.")
    description: str = Field(description="Detailed description including the setting, style/attire, mood, relationships, and any cultural or political anchors of that time that relate to the subject or photo.")
    size: str = Field(description="The physical size of the photo in inches (e.g., '2.5x3.5in').")

def identify_front_back(image_paths: list[Path]) -> tuple[Path, Path]:
    if len(image_paths) != 2:
        raise ValueError(f"Expected exactly 2 images in directory, found {len(image_paths)}")
        
    path1, path2 = image_paths[0], image_paths[1]
    name1_lower = path1.name.lower()
    name2_lower = path2.name.lower()
    
    # Check for explicit "front" and "back" in filenames
    if "front" in name1_lower and "back" in name2_lower:
        return path1, path2
    if "back" in name1_lower and "front" in name2_lower:
        return path2, path1
        
    # Check for "a" and "b" suffixes before extension (e.g. photo_a.jpg, photo_b.jpg)
    if name1_lower.endswith("a.jpg") or name1_lower.endswith("a.jpeg"):
        if name2_lower.endswith("b.jpg") or name2_lower.endswith("b.jpeg"):
            return path1, path2
    if name2_lower.endswith("a.jpg") or name2_lower.endswith("a.jpeg"):
        if name1_lower.endswith("b.jpg") or name1_lower.endswith("b.jpeg"):
            return path2, path1
            
    # Default fallback: sort alphabetically
    sorted_paths = sorted(image_paths)
    return sorted_paths[0], sorted_paths[1]

def process_single_folder(subfolder_path: Path, force: bool = False) -> bool:
    """Processes a single directory containing exactly 2 images (front and back)."""
    metadata_file_path = subfolder_path / "metadata.json"
    if metadata_file_path.exists() and not force:
        print(f"[*] Skipping {subfolder_path.name}: metadata.json already exists (use --force to regenerate).")
        return True

    images = [f for f in subfolder_path.iterdir() if f.is_file() and f.suffix.lower() in ('.jpg', '.jpeg')]
    if len(images) != 2:
        print(f"[-] Skipping {subfolder_path.name}: Expected 2 images, found {len(images)}.")
        return False

    try:
        front_path, back_path = identify_front_back(images)
    except Exception as e:
        print(f"[-] Error identifying front/back in {subfolder_path.name}: {e}")
        return False

    print(f"\nProcessing folder: {subfolder_path.name}")
    print(f"  Front photo: {front_path.name}")
    print(f"  Back photo:  {back_path.name}")

    try:
        with Image.open(front_path) as img:
            width_px, height_px = img.size
    except Exception as e:
        print(f"[-] Error opening image {front_path.name}: {e}")
        return False

    dpi = 300
    width_in = width_px / dpi
    height_in = height_px / dpi
    size_str = f"{width_in:.1f}x{height_in:.1f}in"

    try:
        front_img = Image.open(front_path)
        back_img = Image.open(back_path)
    except Exception as e:
        print(f"[-] Error loading images for {subfolder_path.name}: {e}")
        return False

    prompt = (
        "Analyze these two sides of a scanned photograph (front and back). "
        "Please extract and synthesize metadata about this photo. "
        "Things like the year, decade, a list of subjects, estimated location, image_color, photo_type, etc."
        "Provide a title strictly under 80 characters including key search terms (decade, subject, photo type, color, location). The title MUST always include the word 'Photo' (e.g. ending with 'Snapshot Photo' or 'Vintage Photo'). Make sure to prioritize the subject of the photo, then append attributes and keywords after a comma. "
        "a verbatim transcription of any handwritten notes, stamps, or text on the back in 'back_text_transcription' (or empty string if none), "
        "a detailed description including the setting, any cultural or political anchors of that time, "
        "Also select 1 to 3 relevant themes from the allowed list: " + ", ".join(EBAY_THEMES) + ". "
        f"The physical size of the photo has been calculated as {size_str}. Include this exactly in the 'size' field."
    )

    print(f"Requesting metadata from Gemini (gemini-3.7-flash) for {subfolder_path.name}...")
    client = get_client()

    try:
        response = client.models.generate_content(
            model='gemini-3.7-flash',
            contents=[prompt, front_img, back_img],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PhotoMetadata,
                temperature=0.4,
            ),
        )

        metadata = response.text
        if metadata is None:
            raise ValueError("Received an empty response from Gemini.")

        json_obj = json.loads(metadata)
        
        # Hardcode firm price without consulting Gemini
        json_obj["price"] = DEFAULT_PRICE

        # Ensure 'Photo' is always included in the title as a safety net
        title = json_obj.get("title", "").strip()
        if "photo" not in title.lower():
            title = f"{title} Photo"
            if len(title) > 80:
                title = title[:80]
            json_obj["title"] = title

        json_obj["sku"] = build_ebay_safe_sku(subfolder_path.name)
        with open(metadata_file_path, "w") as f:
            json.dump(json_obj, f, indent=4)

        print(f"[+] Successfully wrote metadata to {metadata_file_path.name}")
        return True

    except Exception as e:
        print(f"[-] Error processing {subfolder_path.name}: {e}")
        return False

def generate_metadata(folder_path: str, force: bool = False):
    subfolder_path = Path(folder_path).resolve()
    if not subfolder_path.exists() or not subfolder_path.is_dir():
        print(f"Error: The path '{folder_path}' is not a valid directory.")
        sys.exit(1)

    # 1. Direct check: if target directory itself contains exactly 2 images
    direct_images = [f for f in subfolder_path.iterdir() if f.is_file() and f.suffix.lower() in ('.jpg', '.jpeg')]
    if len(direct_images) == 2:
        process_single_folder(subfolder_path, force=force)
    else:
        # 2. Recursive check: scan all child directories for folders containing 2 images
        print(f"[*] Target '{subfolder_path.name}' does not contain 2 images directly. Recursively scanning child directories...")
        
        candidate_folders = []
        for root, dirs, files in os.walk(subfolder_path):
            root_path = Path(root)
            if root_path.name.startswith(".") or root_path.name == "cheap_photos":
                continue
            jpg_files = [f for f in files if Path(f).suffix.lower() in ('.jpg', '.jpeg')]
            if len(jpg_files) == 2:
                candidate_folders.append(root_path)

        candidate_folders = sorted(candidate_folders)

        if not candidate_folders:
            print(f"[-] No child directories containing 2 images were found under '{subfolder_path}'.")
            return

        print(f"[*] Found {len(candidate_folders)} photo folder(s) to process.")
        processed_count = 0
        for folder in candidate_folders:
            if process_single_folder(folder, force=force):
                processed_count += 1

        print(f"\n[*] Batch metadata generation complete for '{subfolder_path.name}'. Processed {processed_count}/{len(candidate_folders)} folders.")

    # Automatically update master listing_log.md dashboard
    try:
        from run_ebay_workflow import update_markdown_log
        update_markdown_log(subfolder_path)
    except Exception as e:
        print(f"[*] Note: Could not update listing_log.md: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate metadata for organized photos using Gemini.")
    parser.add_argument("directory", help="Path to directory containing photo pair or parent directory")
    parser.add_argument("--force", "-f", action="store_true", help="Force regeneration of metadata even if metadata.json already exists")
    args = parser.parse_args()
    
    generate_metadata(args.directory, force=args.force)

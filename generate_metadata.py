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

# Initialize the Gemini client
# Requires GEMINI_API_KEY environment variable to be set
client = genai.Client()

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
    return re.sub(r"[^A-Za-z0-9]", "", folder_name)[:50]

class PhotoMetadata(BaseModel):
    title: str = Field(description="Title of the photo. It should clearly capture the essence of the photo's content, and include the year and location if possible (e.g., 'Family Picnic in Central Park, 1955').")
    year: str = Field(description="Year the photo was taken (estimated if unknown).")
    subjects: list[str] = Field(description="Subjects describing the photo.")
    themes: list[str] = Field(description="List of 1 to 3 relevant themes selected from the allowed list, ordered by relevance.")
    location: str = Field(description="Location where the photo was taken (estimated or general location/city).")
    description: str = Field(description="Detailed description including the setting and any cultural or political anchors of that time.")
    size: str = Field(description="The physical size of the photo in inches (e.g., '2.5x3.5in').")
    price: str = Field(description="Estimated price of the photo based on its content, condition, and market trends. It should be a single value, not a range, and should be expressed in USD (e.g., '$15').")

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

def generate_metadata(folder_path: str):
    subfolder_path = Path(folder_path).resolve()
    if not subfolder_path.exists() or not subfolder_path.is_dir():
        print(f"Error: The path '{folder_path}' is not a valid directory.")
        sys.exit(1)
        
    # Get all JPG/JPEG files inside the directory
    images = [f for f in subfolder_path.iterdir() if f.is_file() and f.suffix.lower() in ('.jpg', '.jpeg')]
    
    if len(images) != 2:
        print(f"Error: Expected exactly 2 images (front and back) in '{subfolder_path}', but found {len(images)}.")
        sys.exit(1)
        
    try:
        front_path, back_path = identify_front_back(images)
    except Exception as e:
        print(f"Error identifying front/back: {e}")
        sys.exit(1)
        
    print(f"Processing folder: {subfolder_path.name}")
    print(f"  Front photo: {front_path.name}")
    print(f"  Back photo:  {back_path.name}")
    
    # Get dimensions from the front photo
    with Image.open(front_path) as img:
        width_px, height_px = img.size
        
    # Calculate size in inches assuming 300dpi
    dpi = 300
    width_in = width_px / dpi
    height_in = height_px / dpi
    size_str = f"{width_in:.1f}x{height_in:.1f}in"
    
    # Open both images for Gemini
    front_img = Image.open(front_path)
    back_img = Image.open(back_path)
    
    prompt = (
        "Analyze these two sides of a scanned photograph (front and back). "
        "Please extract and synthesize metadata about this photo. "
        "Provide a descriptive title, estimated year, a list of subjects, estimated location, "
        "a detailed description including the setting and any cultural or political anchors of that time, "
        "and estimated price based on the content and condition of the photo. "
        "Also select 1 to 3 relevant themes from the allowed list: " + ", ".join(EBAY_THEMES) + ". "
        "If handwriting or stamps are on the back, use them to inform your metadata. "
        f"The physical size of the photo has been calculated as {size_str}. Include this exactly in the 'size' field."
    )
    
    print(f"Requesting metadata from Gemini (gemini-3.5-flash) for {subfolder_path.name}...")
    
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash',
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
        
        # Write metadata to json file
        metadata_file_path = subfolder_path / "metadata.json"
        with open(metadata_file_path, "w") as f:
            # Format it nicely
            json_obj = json.loads(metadata)
            json_obj["sku"] = build_ebay_safe_sku(subfolder_path.name)
            json.dump(json_obj, f, indent=4)
            
        print(f"Successfully wrote metadata to {metadata_file_path.relative_to(subfolder_path.parent.parent if subfolder_path.parent.parent.exists() else subfolder_path.parent)}\n")
        
    except Exception as e:
        print(f"Error processing {subfolder_path.name}: {e}\n")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate metadata for organized photos using Gemini.")
    parser.add_argument("directory", help="Path to the directory containing both front and back photos")
    args = parser.parse_args()
    
    generate_metadata(args.directory)

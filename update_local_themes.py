import os
import json
import argparse
import concurrent.futures
from pathlib import Path
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Initialize Gemini Client (requires GEMINI_API_KEY env var)
client = genai.Client()

# Official eBay themes for category 262421
EBAY_THEMES = [
    "Advertising",
    "Agriculture",
    "Americana",
    "Amusement Parks",
    "Animals",
    "Animation",
    "Anime & Manga",
    "Architecture",
    "Art",
    "Astrology",
    "Automobilia",
    "Aviation",
    "Celebrities",
    "Cities & Towns",
    "Comics",
    "Community Life",
    "Conflicts & Wars",
    "Continents & Countries",
    "Cultures & Ethnicities",
    "Cycling",
    "Dance",
    "Disasters",
    "Domestic & Family Life",
    "Events & Festivals",
    "Exhibitions",
    "Fairy Tales",
    "Famous Places",
    "Fantasy",
    "Fashion",
    "Fire Department",
    "Floral",
    "Food & Drink",
    "Glamor",
    "Graffiti",
    "History",
    "Hobbies & Leisure",
    "Holidays",
    "Industrial",
    "Inspirational",
    "Judaica",
    "Language",
    "Law Enforcement",
    "Leisure",
    "Love",
    "Maritime",
    "Militaria",
    "Movies",
    "Music",
    "Mythological",
    "Natural History",
    "Nature",
    "Nautical",
    "Patriotic",
    "People",
    "Politics",
    "Portrait",
    "Railroadiana",
    "Religious",
    "Risqué",
    "Royalty",
    "Science & Medicine",
    "Social History",
    "Sports",
    "Stamps",
    "Technology",
    "Television",
    "Theater",
    "Topographical",
    "Transportation",
    "Travel",
    "Travel & Transportation",
    "Universities",
    "Video Games",
    "Western",
    "Working Life",
]


class ThemeSelection(BaseModel):
    themes: list[str] = Field(
        description="A list of 1 to 3 relevant themes selected from the allowed list, ordered by relevance."
    )


def get_best_themes(title: str, description: str, subjects: list) -> list:
    prompt = (
        "Based on the following photo metadata, select up to 3 relevant themes (minimum 1, maximum 3) "
        "from the list of allowed eBay themes below, ordered by how strongly they apply to the photo.\n\n"
        f"Photo Title: {title}\n"
        f"Photo Description: {description}\n"
        f"Subjects: {', '.join(subjects)}\n\n"
        f"Allowed eBay Themes:\n{', '.join(EBAY_THEMES)}\n\n"
        "You must only select themes from the allowed list."
    )
    print(f"Requesting Themes from Gemini (gemini-2.5-flash)...")

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ThemeSelection,
                temperature=0.1,
            ),
        )

        metadata_text = response.text
        if not metadata_text:
            raise ValueError("Empty response text from Gemini API")
        result = json.loads(metadata_text)
        themes = result.get("themes", [])
        # Filter to ensure only valid themes are kept, and cap at 3
        valid_themes = [t for t in themes if t in EBAY_THEMES][:3]
        if not valid_themes:
            return ["People"]  # Default fallback
        return valid_themes
    except Exception as e:
        print(f"Gemini API error: {e}")
        return ["People"]


def process_folder(folder: Path):
    metadata_file = folder / "metadata.json"
    if not metadata_file.exists():
        return

    try:
        with open(metadata_file, "r") as f:
            data = json.load(f)

        # We want to re-run it for all files (overwrite the previous "theme" and "themes" keys)
        themes = get_best_themes(
            title=data.get("title", ""),
            description=data.get("description", ""),
            subjects=data.get("subjects", []),
        )

        # Keep both singular theme (first element) and plural themes for safety
        # data["theme"] = themes[0] if themes else "People"
        data["theme"] = themes

        with open(metadata_file, "w") as f:
            json.dump(data, f, indent=4)

        print(f"Processed {folder.name} -> {themes}")

    except Exception as e:
        print(f"Error processing {folder.name}: {e}")


def update_local_metadata(directory_path: str):
    dir_path = Path(directory_path)
    subfolders = sorted(
        [f for f in dir_path.iterdir() if f.is_dir() and not f.name.startswith(".")]
    )

    if not subfolders:
        print(
            f"No subfolders found. Running on the directory itself: {dir_path.resolve()}"
        )
        process_folder(dir_path)
        return

    print(
        f"Found {len(subfolders)} folders. Starting classification using concurrent threads..."
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(process_folder, subfolders)

    print("Done processing all folders!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Update existing metadata.json files in subfolders of the specified directory with "
            "classification of 1-3 highly relevant eBay themes, using Gemini 2.5 Flash "
            "or checking against official eBay themes."
        )
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default="/Users/agustinbjr/Ebay/EPSCAN",
        help="Path to the EPSCAN directory containing item subfolders (defaults to /Users/agustinbjr/Ebay/EPSCAN)",
    )
    args = parser.parse_args()

    update_local_metadata(args.directory)

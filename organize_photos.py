"""Organize scanned photo pairs into individual subfolders.

This script scans a directory for sequential JPG or JPEG files, treats each
adjacent pair as the front and back of a single photograph, and moves each pair
into a subfolder named after the front image.

Usage:
- python organize_photos.py /path/to/photo-directory
- If no directory is provided, the script defaults to the test directory in the script's folder.
"""

import sys
import shutil
import os
from pathlib import Path

def is_organized_dir(path: Path) -> bool:
    """Check if a directory appears to be an already organized photo folder."""
    if (path / "metadata.json").exists():
        return True
    try:
        for f in path.iterdir():
            if f.is_file() and f.suffix.lower() in ('.jpg', '.jpeg'):
                if f.stem == path.name:
                    return True
    except OSError:
        pass
    return False

def organize_photos_in_dir(dir_path: Path) -> int:
    # Get all JPG/JPEG files, sort them to ensure correct pairing
    files = sorted([f for f in dir_path.iterdir() if f.is_file() and f.suffix.lower() in ('.jpg', '.jpeg')])
    
    # Check if we have an even number of files
    if len(files) % 2 != 0:
        print(f"Warning: Odd number of files found in '{dir_path}'. The last file might not have a pair.")
        
    pairs = []
    for i in range(0, len(files) - 1, 2):
        pairs.append((files[i], files[i+1]))
    
    if not pairs:
        return 0
        
    print(f"\nOrganizing {len(pairs)} pairs in '{dir_path}':")
    for front_path, back_path in pairs:
        print(f"Organizing pair: {front_path.name} and {back_path.name}")
        
        # Create subfolder based on the front file's name without extension
        subfolder_name = front_path.stem
        subfolder_path = dir_path / subfolder_name
        subfolder_path.mkdir(exist_ok=True)
        
        # Move files to subfolder
        new_front_path = subfolder_path / front_path.name
        new_back_path = subfolder_path / back_path.name
        
        if front_path.exists():
            shutil.move(str(front_path), str(new_front_path))
            print(f"  Moved {front_path.name} -> {new_front_path.relative_to(dir_path)}")
        if back_path.exists():
            shutil.move(str(back_path), str(new_back_path))
            print(f"  Moved {back_path.name} -> {new_back_path.relative_to(dir_path)}")
            
    return len(pairs)

def organize_photos(directory_path: str):
    dir_path = Path(directory_path)
    if not dir_path.exists():
        print(f"Error: Directory '{directory_path}' does not exist.")
        sys.exit(1)
        
    # Check if the root directory itself is already organized
    if is_organized_dir(dir_path):
        print(f"Directory '{directory_path}' appears to be already organized. Skipping.")
        return

    total_pairs_organized = 0
    skipped_dirs = []

    # Walk the directory tree top-down
    for root, dirs, files in os.walk(dir_path, topdown=True):
        current_dir = Path(root)
        
        # Identify subdirectories to prune vs keep
        keep_dirs = []
        for d in dirs:
            if d.startswith('.') or d == '__pycache__':
                continue
            sub_path = current_dir / d
            if is_organized_dir(sub_path):
                skipped_dirs.append(sub_path)
                print(f"Skipping already organized directory: {sub_path}")
            else:
                keep_dirs.append(d)
                
        dirs[:] = keep_dirs
        
        # Organize photos in the current directory
        total_pairs_organized += organize_photos_in_dir(current_dir)

    if total_pairs_organized == 0:
        if skipped_dirs:
            print(f"\nNo unorganized photo pairs found ({len(skipped_dirs)} subdirectories are already organized).")
        else:
            print("No valid pairs of photos found in the directory.")

if __name__ == "__main__":
    script_dir = Path(__file__).parent.resolve()
    directory = sys.argv[1] if len(sys.argv) > 1 else str(script_dir / "test")
    organize_photos(directory)

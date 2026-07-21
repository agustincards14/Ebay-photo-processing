#!/usr/bin/env python3
import sys
import os
import subprocess
from pathlib import Path


def main():
    # Ensure both directory and rotation angle are provided
    if len(sys.argv) < 3:
        print("Usage: python fix_orientations.py <degrees> <directory>")
        sys.exit(1)

    try:
        degrees = int(sys.argv[1])
    except ValueError:
        print(f"Error: Rotation value '{sys.argv[1]}' must be an integer.")
        sys.exit(1)

    dir_path = Path(sys.argv[2])

    if not dir_path.is_dir():
        print(f"Error: Directory '{dir_path}' does not exist or is not a directory.")
        sys.exit(1)

    # Find JPEG files (matching .jpg and .jpeg, case-insensitive)
    jpeg_extensions = {".jpg", ".jpeg"}
    image_files = sorted(
        [
            p
            for p in dir_path.iterdir()
            if p.is_file() and p.suffix.lower() in jpeg_extensions
        ]
    )

    if not image_files:
        print(f"No JPEG images found in directory '{dir_path}'.")
        sys.exit(0)

    print(
        f"Found {len(image_files)} JPEG images in '{dir_path}'. Rotating by {degrees} degrees..."
    )

    success_count = 0
    for img in image_files:
        print(f"Rotating {img.name}...")
        try:
            # sips -r <degrees> <image_path>
            cmd = ["sips", "-r", str(degrees), str(img)]
            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print(f"  Successfully rotated {img.name}")
            success_count += 1
        except subprocess.CalledProcessError as e:
            print(f"  Error: Failed to rotate {img.name} using sips: {e}")
        except Exception as e:
            print(f"  Error rotating {img.name}: {e}")

    print(f"Done. Successfully rotated {success_count} of {len(image_files)} files.")


if __name__ == "__main__":
    main()

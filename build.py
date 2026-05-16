"""
Build script: parses all data and copies static files to dist/ folder
ready for deployment to any static hosting (GitHub Pages, Netlify, etc.)
"""
import shutil
import os
import subprocess
import sys

DIST_DIR = "dist"

def main():
    # 1. Parse all data
    print("Parsing results...")
    result = subprocess.run([sys.executable, "parse_results.py"], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        sys.exit(1)

    # 2. Clean and create dist folder
    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
    os.makedirs(DIST_DIR)

    # 3. Copy static files
    shutil.copy("index.html", DIST_DIR)

    # 4. Copy data folder
    shutil.copytree("data", os.path.join(DIST_DIR, "data"))

    print(f"\nBuild complete! Static site ready in '{DIST_DIR}/'")
    print(f"Files:")
    for root, dirs, files in os.walk(DIST_DIR):
        for f in files:
            path = os.path.join(root, f)
            size = os.path.getsize(path)
            print(f"  {path} ({size:,} bytes)")


if __name__ == "__main__":
    main()

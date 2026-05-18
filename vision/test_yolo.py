#!/usr/bin/env python3
"""
test_yolo.py — Quick smoke test for YOLO + CMS pipeline.
Downloads a test image and runs the full ingest pipeline in dry-run mode.

Run from vision_env:
    source ~/vision_env/bin/activate
    python3 ~/projectbrain_dev/vision/test_yolo.py
"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_IMAGE_URL = "https://ultralytics.com/images/zidane.jpg"
TEST_IMAGE     = Path("/tmp/vision_test.jpg")


def main():
    print("=== Vision pipeline smoke test ===\n")

    # Download test image
    if not TEST_IMAGE.exists():
        print(f"Downloading test image → {TEST_IMAGE}")
        urllib.request.urlretrieve(TEST_IMAGE_URL, TEST_IMAGE)
    else:
        print(f"Using cached test image: {TEST_IMAGE}")

    # Run dry-run ingest
    sys.argv = ["test_yolo.py", str(TEST_IMAGE), "--dry-run"]
    from vision.image_ingest import ingest_image
    result = ingest_image(str(TEST_IMAGE))

    print(f"\nResult: {result}")

    if result["objects"] > 0:
        print("\n✓ YOLO detection working")
    else:
        print("\n✗ No objects detected — check model download")

    if result["relations"] >= 0:
        print("✓ Pipeline reached relation stage")


if __name__ == "__main__":
    main()

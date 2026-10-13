"""Automated test suite for the EPSCAN Instagram Carousel Publishing Workflow."""

import os
import sys
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instagram_caption_generator import (
    build_carousel_caption,
    clean_hashtag,
    MAX_INSTAGRAM_CAPTION_LENGTH,
)
from instagram_client import InstagramClient, InstagramGraphAPIError
from run_instagram_workflow import (
    get_front_image_url,
    find_item_folders,
    load_item_metadata,
    process_batch,
)


class TestInstagramCaptionGenerator(unittest.TestCase):
    def setUp(self):
        self.mock_items = [
            {
                "sku": f"AIPH-TEST_000{i}",
                "title": f"Vintage Snapshot #{i} of City Street",
                "year": str(1950 + i * 5),
                "location": "Chicago, Illinois",
                "description": f"Detailed historical description for photograph #{i}. A group of people walking.",
                "subjects": ["Street", "People", "City"],
                "themes": ["Urban Life", "Americana"],
                "image_color": "Black & White",
            }
            for i in range(1, 6)
        ]

    def test_caption_contains_all_sequential_slides(self):
        caption = build_carousel_caption(self.mock_items)
        self.assertIn("[1/5] 1955: Vintage Snapshot #1 of City Street", caption)
        self.assertIn("[2/5] 1960: Vintage Snapshot #2 of City Street", caption)
        self.assertIn("[3/5] 1965: Vintage Snapshot #3 of City Street", caption)
        self.assertIn("[4/5] 1970: Vintage Snapshot #4 of City Street", caption)
        self.assertIn("[5/5] 1975: Vintage Snapshot #5 of City Street", caption)
        self.assertTrue(caption.startswith("[1/5]"))
        self.assertIn("🏷️", caption)

    def test_caption_length_enforces_limit_with_huge_descriptions(self):
        huge_items = [
            {
                "title": f"Very Long Vintage Title Number {i}",
                "year": "1960",
                "location": "New York",
                "description": "A very long repetitive description. " * 80,
                "subjects": [f"Subject_{i}_{j}" for j in range(10)],
                "themes": ["History", "Architecture"],
            }
            for i in range(1, 6)
        ]
        caption = build_carousel_caption(huge_items)
        self.assertLessEqual(len(caption), MAX_INSTAGRAM_CAPTION_LENGTH)
        self.assertIn("[1/5]", caption)
        self.assertIn("[5/5]", caption)

    def test_clean_hashtag(self):
        self.assertEqual(clean_hashtag("Black & White"), "#BlackWhite")
        self.assertEqual(clean_hashtag("1950s Car!"), "#1950sCar")
        self.assertEqual(clean_hashtag("   "), "")

    def test_select_exact_six_hashtags(self):
        from instagram_caption_generator import select_exact_hashtags, DEFAULT_BASE_HASHTAGS
        tags = select_exact_hashtags(self.mock_items, seed=42)
        self.assertEqual(len(tags), 6)
        # Exactly 2 from base community tags
        base_matches = [t for t in tags if t in DEFAULT_BASE_HASHTAGS]
        self.assertEqual(len(base_matches), 2)
        # All tags properly formatted
        self.assertTrue(all(t.startswith("#") for t in tags))
        # Unique tags
        self.assertEqual(len(set(tags)), 6)


class TestURLValidationAndExitCode(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_front_image_url_success(self):
        item_dir = self.test_dir / "item_01"
        item_dir.mkdir()
        inv_payload = {
            "product": {
                "imageUrls": [
                    "https://i.ebayimg.com/00/s/MTAwMFgxMDAw/z/valid_front.jpg",
                    "https://i.ebayimg.com/00/s/MTAwMFgxMDAw/z/valid_back.jpg",
                ]
            }
        }
        with open(item_dir / "inventory_item.json", "w") as f:
            json.dump(inv_payload, f)

        url = get_front_image_url(item_dir)
        self.assertEqual(url, "https://i.ebayimg.com/00/s/MTAwMFgxMDAw/z/valid_front.jpg")

    def test_get_front_image_url_missing(self):
        item_dir = self.test_dir / "item_no_url"
        item_dir.mkdir()
        self.assertIsNone(get_front_image_url(item_dir))

        # With empty imageUrls
        with open(item_dir / "inventory_item.json", "w") as f:
            json.dump({"product": {"imageUrls": []}}, f)
        self.assertIsNone(get_front_image_url(item_dir))

    def test_process_batch_exits_when_image_url_missing(self):
        # Create 5 item folders where 1 is missing inventory_item.json
        batch_folders = []
        for i in range(1, 6):
            folder = self.test_dir / f"folder_{i}"
            folder.mkdir()
            with open(folder / "metadata.json", "w") as f:
                json.dump({"title": f"Photo {i}", "year": "1960"}, f)

            if i != 3:  # Folder 3 has NO inventory_item.json
                with open(folder / "inventory_item.json", "w") as f:
                    json.dump({"product": {"imageUrls": [f"https://i.ebayimg.com/img_{i}.jpg"]}}, f)

            batch_folders.append(folder)

        # Must exit with code 1
        with self.assertRaises(SystemExit) as cm:
            process_batch(batch_folders, client=None, dry_run=True)
        self.assertEqual(cm.exception.code, 1)

    def test_is_already_posted_to_instagram(self):
        from run_instagram_workflow import is_already_posted_to_instagram
        self.assertFalse(is_already_posted_to_instagram({}))
        self.assertFalse(is_already_posted_to_instagram({"title": "Vintage Photo"}))
        self.assertTrue(is_already_posted_to_instagram({"instagram_url": "https://www.instagram.com/p/ABC123xyz/"}))
        self.assertTrue(is_already_posted_to_instagram({"instagram_permalink": "https://www.instagram.com/p/ABC123xyz/"}))
        self.assertTrue(is_already_posted_to_instagram({"instagram_posted": True}))

    def test_update_item_metadata_post_success_saves_url(self):
        from run_instagram_workflow import update_item_metadata_post_success
        item_dir = self.test_dir / "item_update"
        item_dir.mkdir()
        meta_path = item_dir / "metadata.json"
        with open(meta_path, "w") as f:
            json.dump({"title": "Test Photo", "sku": "AIPH-01"}, f)

        test_url = "https://www.instagram.com/p/C-TestPost123/"
        update_item_metadata_post_success(
            folder_path=item_dir,
            post_url=test_url
        )

        with open(meta_path, "r") as f:
            updated = json.load(f)

        self.assertEqual(updated.get("instagram_url"), test_url)
        self.assertNotIn("instagram_posted", updated)
        self.assertNotIn("instagram_carousel_id", updated)
        self.assertNotIn("instagram_posted_at", updated)



class TestInstagramClientMock(unittest.TestCase):
    def setUp(self):
        self.client = InstagramClient(access_token="test_token", instagram_account_id="123456789")

    @patch("requests.post")
    @patch("requests.get")
    def test_post_carousel_flow(self, mock_get, mock_post):
        # 1. Mock child container creations
        post_resp_child = MagicMock()
        post_resp_child.status_code = 200
        post_resp_child.json.return_value = {"id": "child_container_101"}

        # 2. Mock carousel container creation
        post_resp_carousel = MagicMock()
        post_resp_carousel.status_code = 200
        post_resp_carousel.json.return_value = {"id": "carousel_container_202"}

        # 3. Mock publish
        post_resp_publish = MagicMock()
        post_resp_publish.status_code = 200
        post_resp_publish.json.return_value = {"id": "media_live_303"}

        mock_post.side_effect = [
            post_resp_child,
            post_resp_child,
            post_resp_child,
            post_resp_child,
            post_resp_child,
            post_resp_carousel,
            post_resp_publish,
        ]

        # 4. Mock status polls (FINISHED) and permalink
        get_status_finished = MagicMock()
        get_status_finished.status_code = 200
        get_status_finished.json.return_value = {"status_code": "FINISHED", "status": "Ready"}

        get_permalink = MagicMock()
        get_permalink.status_code = 200
        get_permalink.json.return_value = {"id": "media_live_303", "permalink": "https://www.instagram.com/p/ABC123xyz/"}

        mock_get.side_effect = [
            get_status_finished,
            get_status_finished,
            get_status_finished,
            get_status_finished,
            get_status_finished,
            get_status_finished,  # For carousel container
            get_permalink,
        ]

        image_urls = [f"https://i.ebayimg.com/photo_{i}.jpg" for i in range(1, 6)]
        result = self.client.post_carousel(image_urls=image_urls, caption="Test caption")

        self.assertEqual(result["media_id"], "media_live_303")
        self.assertEqual(result["permalink"], "https://www.instagram.com/p/ABC123xyz/")


if __name__ == "__main__":
    unittest.main()

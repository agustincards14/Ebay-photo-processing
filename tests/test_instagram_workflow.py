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
        self.items = [dict(title=f"Original Photo {i}", description="A longer description.",
                           year="circa 1940", themes=["Family", "Leisure"], subjects=["Children", "Garden"])
                      for i in range(1, 8)]
        self.copy = {"photos": [dict(photo_number=i, title=f"Family scene {i}",
                                    summary="Children gather in a garden.") for i in range(1, 8)]}
        self.client_patch = patch("instagram_caption_generator.genai.Client")
        self.client_factory = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.generate = self.client_factory.return_value.__enter__.return_value.models.generate_content
        self.generate.return_value = MagicMock(text=json.dumps(self.copy))
        self.env_patch = patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_ai_titles_and_summaries_with_all_seven_photos(self):
        caption = build_carousel_caption(self.items)
        for i in range(1, 8):
            self.assertIn(f"[{i}/7] Family scene {i} — Children gather in a garden.", caption)
        self.assertLessEqual(len(caption), 800)
        self.assertEqual(len(caption.split("🏷️ ")[1].split()), 5)
        config = self.generate.call_args.kwargs["config"]
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertIsNotNone(config.response_schema)

    def test_rewrites_oversized_copy_without_truncating(self):
        oversized = {"photos": [dict(photo, summary="Long description " * 80) for photo in self.copy["photos"]]}
        self.generate.side_effect = [MagicMock(text=json.dumps(oversized)), MagicMock(text=json.dumps(self.copy))]
        caption = build_carousel_caption(self.items)
        self.assertEqual(self.generate.call_count, 2)
        self.assertLessEqual(len(caption), 800)
        self.assertNotIn("…", caption)
        self.assertIn("failed validation", self.generate.call_args.kwargs["contents"])

    def test_missing_or_reordered_photos_are_rejected(self):
        self.generate.return_value.text = json.dumps({"photos": list(reversed(self.copy["photos"]))})
        with self.assertRaisesRegex(ValueError, "three attempts"):
            build_carousel_caption(self.items)
        self.assertEqual(self.generate.call_count, 3)

    def test_invalid_json_never_cached(self):
        self.generate.return_value.text = "not JSON"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_carousel_caption(self.items, cache_dir=Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cache_reused_without_api_key_and_invalidated_on_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            original = build_carousel_caption(self.items, cache_dir=cache_dir)
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(build_carousel_caption(self.items, cache_dir=cache_dir), original)
            self.assertEqual(self.generate.call_count, 1)
            self.items[0]["description"] = "New source description."
            build_carousel_caption(self.items, cache_dir=cache_dir)
            self.assertEqual(self.generate.call_count, 2)
            build_carousel_caption(self.items, cache_dir=cache_dir, item_ids=[str(i) for i in range(7)])
            self.assertEqual(self.generate.call_count, 3)

    def test_tampered_cache_is_regenerated(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            original = build_carousel_caption(self.items, cache_dir=cache_dir)
            cache_file = next(cache_dir.glob("*.json"))
            cached = json.loads(cache_file.read_text())
            cached["caption"] = "Unexpected caption"
            cache_file.write_text(json.dumps(cached))
            self.assertEqual(build_carousel_caption(self.items, cache_dir=cache_dir), original)
            self.assertEqual(self.generate.call_count, 2)

    def test_missing_api_key_fails_before_request(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
                build_carousel_caption(self.items)
        self.generate.assert_not_called()

    def test_hashtag_category_mix(self):
        from instagram_caption_generator import select_exact_hashtags
        tags = select_exact_hashtags(self.items, seed=42)
        self.assertEqual(tags[0], "#VintagePhotos")
        self.assertEqual(set(tags[1:3]), {"#Family", "#Leisure"})
        self.assertEqual(set(tags[3:]), {"#Children", "#Garden"})
        self.assertEqual(len(set(tag.lower() for tag in tags)), 5)

    def test_clean_hashtag(self):
        self.assertEqual(clean_hashtag("Black & White"), "#BlackWhite")
        self.assertEqual(clean_hashtag("1950s Car!"), "#1950sCar")
        self.assertEqual(clean_hashtag("   "), "")


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
        # Create 7 item folders where 1 is missing inventory_item.json
        batch_folders = []
        for i in range(1, 8):
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



class TestInstagramBatchSelection(unittest.TestCase):
    def test_preferred_and_fallback_batches_after_posted_filter(self):
        from run_instagram_workflow import main
        for available, expected in ((9, 7), (7, 7), (6, 6), (5, 5), (4, None), (1, None)):
            with self.subTest(available=available), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                # Already-posted photos must not count toward the batch size.
                for idx in range(available + 3):
                    folder = root / f"photo_{idx:02}"
                    folder.mkdir()
                    meta = {"title": "Test Photo"}
                    if idx < 3:
                        meta["instagram_url"] = "https://www.instagram.com/p/already/"
                    (folder / "metadata.json").write_text(json.dumps(meta))
                with patch.object(sys, "argv", ["run_instagram_workflow.py", directory, "--dry-run"]), \
                     patch("run_instagram_workflow.process_batch", return_value=True) as process, \
                     patch("builtins.print"):
                    if expected is None:
                        with self.assertRaises(SystemExit) as exc:
                            main()
                        self.assertEqual(exc.exception.code, 1)
                        process.assert_not_called()
                    else:
                        main()
                        selected = process.call_args.kwargs["batch_folders"]
                        self.assertEqual(len(selected), expected)
                        self.assertEqual(selected[0].name, "photo_03")
                        process.assert_called_once()
                        self.assertTrue(process.call_args.kwargs["dry_run"])


class TestInstagramLogLocation(unittest.TestCase):
    def test_logs_only_beside_epscan_listing_logs(self):
        from run_instagram_workflow import append_to_instagram_log
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            folders = []
            for name in ("EPSCAN_ONE", "EPSCAN_TWO"):
                root = project / name
                item = root / "group" / "photo"
                item.mkdir(parents=True)
                (root / "listing_log.md").write_text("Listing log")
                folders.append(item)
            with patch("run_instagram_workflow.PROJECT_ROOT", project):
                append_to_instagram_log(folders[0].parent, folders, "post123", "https://example.com/post", "caption")
            for folder in folders:
                self.assertIn("post123", (folder.parents[1] / "instagram_log.md").read_text())
                self.assertFalse((folder.parent / "instagram_log.md").exists())
            self.assertFalse((project / "instagram_log.md").exists())

    def test_refuses_directories_without_epscan_listing_log(self):
        from run_instagram_workflow import find_instagram_log_dir
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "listing_log.md").write_text("Listing log")
            with self.assertRaises(ValueError):
                find_instagram_log_dir(root)
            epscan = root / "EPSCAN_EMPTY"
            epscan.mkdir()
            with self.assertRaises(ValueError):
                find_instagram_log_dir(epscan)


class TestInstagramClientMock(unittest.TestCase):
    def setUp(self):
        self.client = InstagramClient(access_token="test_token", instagram_account_id="123456789")

    @patch("time.sleep")
    @patch("requests.post")
    @patch("requests.get")
    def test_post_carousel_flow(self, mock_get, mock_post, mock_sleep):
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
            get_status_finished,
            get_status_finished,
            get_status_finished,  # For carousel container
            get_permalink,
        ]

        image_urls = [f"https://i.ebayimg.com/photo_{i}.jpg" for i in range(1, 8)]
        result = self.client.post_carousel(image_urls=image_urls, caption="Test caption")

        self.assertEqual(result["media_id"], "media_live_303")
        self.assertEqual(result["permalink"], "https://www.instagram.com/p/ABC123xyz/")

    @patch("time.sleep")
    @patch("requests.post")
    def test_create_item_container_retries_transient_media_error(self, mock_post, mock_sleep):
        # First attempt: Meta fails to download media with error 9004 / 2207052
        resp_fail = MagicMock()
        resp_fail.status_code = 400
        resp_fail.json.return_value = {
            "error": {
                "message": "Only photo or video can be accepted as media type.",
                "code": 9004,
                "error_subcode": 2207052,
                "error_user_msg": "The media could not be fetched from this URI: https://i.ebayimg.com/pic.jpg"
            }
        }
        # Second attempt: Succeeded
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"id": "container_success_999"}

        mock_post.side_effect = [resp_fail, resp_ok]

        cid = self.client.create_item_container("https://i.ebayimg.com/pic.jpg", max_retries=3, base_delay=1.0)
        self.assertEqual(cid, "container_success_999")
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(1.0)

    @patch("requests.post")
    def test_create_item_container_daily_limit_does_not_retry(self, mock_post):
        # Daily limit subcode 2207042 should fail immediately
        resp_limit = MagicMock()
        resp_limit.status_code = 400
        resp_limit.json.return_value = {
            "error": {
                "message": "You reached maximum number of posts that is allowed to be published",
                "code": 9,
                "error_subcode": 2207042
            }
        }
        mock_post.return_value = resp_limit

        with self.assertRaises(InstagramGraphAPIError) as cm:
            self.client.create_item_container("https://i.ebayimg.com/pic.jpg", max_retries=3)

        self.assertIn("Daily Instagram publishing limit reached", str(cm.exception))
        self.assertEqual(mock_post.call_count, 1)


if __name__ == "__main__":
    unittest.main()

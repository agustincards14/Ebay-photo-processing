"""Instagram Graph API Client.

Implements the official Meta Instagram Content Publishing endpoints for creating
and publishing multi-item Carousel posts.
"""

import time
import json
from pathlib import Path
import requests
from typing import Any, Optional

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
DEFAULT_POLL_TIMEOUT_SECS = 60
DEFAULT_POLL_INTERVAL_SECS = 3


class InstagramGraphAPIError(Exception):
    """Raised when an Instagram Graph API request fails."""
    pass


class InstagramClient:
    """Official Meta Instagram Graph API Client for multi-item Carousel publishing."""

    def __init__(
        self,
        access_token: str,
        instagram_account_id: str,
        cache_dir: Optional[Path] = None
    ):
        if not access_token or not instagram_account_id:
            raise ValueError("Both access_token and instagram_account_id are required.")
        self.access_token = access_token
        self.instagram_account_id = instagram_account_id
        self.cache_dir = cache_dir or (Path(__file__).resolve().parent / ".cache")
        self.containers_cache_file = self.cache_dir / "instagram_containers.json"

    def _load_containers_cache(self) -> dict[str, Any]:
        """Loads valid child containers from local cache (under 23 hours old)."""
        if not self.containers_cache_file.exists():
            return {}
        try:
            with open(self.containers_cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            return {
                url: item for url, item in data.items()
                if isinstance(item, dict) and now - item.get("timestamp", 0) < 23 * 3600
            }
        except Exception:
            return {}

    def _save_containers_cache(self, cache_data: dict[str, Any]) -> None:
        """Saves child container mappings to local cache."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temp_file = self.containers_cache_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)
            temp_file.replace(self.containers_cache_file)
        except Exception as e:
            print(f"[!] Warning: Failed to save container cache: {e}")

    def check_container_status(self, container_id: str) -> Optional[str]:
        """Returns the status_code of a container ('FINISHED', 'IN_PROGRESS', 'ERROR') or None."""
        url = f"{GRAPH_API_BASE}/{container_id}"
        params = {"fields": "status_code", "access_token": self.access_token}
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("status_code")
        except Exception:
            pass
        return None

    def create_item_container(
        self,
        image_url: str,
        max_retries: int = 4,
        base_delay: float = 3.0
    ) -> str:
        """
        Creates an individual child media container for a carousel item.
        Checks local cache for recent valid containers before making an API call,
        pre-warms the CDN, and retries with backoff and URL fallback on transient errors.
        
        Args:
            image_url: Publicly accessible HTTPS URL of the image.
            max_retries: Maximum attempts for transient failures.
            base_delay: Initial sleep seconds between retries.
            
        Returns:
            Container ID string.
        """
        # Check cache first for existing valid container
        cache = self._load_containers_cache()
        if image_url in cache:
            cached_cid = cache[image_url].get("id")
            if cached_cid:
                status = self.check_container_status(cached_cid)
                if status == "FINISHED":
                    print(f"    -> [cached] Reusing verified child container {cached_cid}")
                    return cached_cid

        # Prepare candidate URLs (primary and clean fallback if query params present)
        candidate_urls = [image_url]
        if "?" in image_url:
            candidate_urls.append(image_url.split("?")[0])

        url = f"{GRAPH_API_BASE}/{self.instagram_account_id}/media"
        last_error = None

        for attempt in range(1, max_retries + 1):
            target_url = candidate_urls[min(attempt - 1, len(candidate_urls) - 1)]

            # Pre-warm CDN edge cache before Meta fetches it
            try:
                requests.head(target_url, timeout=5)
            except Exception:
                pass

            payload = {
                "image_url": target_url,
                "is_carousel_item": "true",
                "access_token": self.access_token
            }

            try:
                resp = requests.post(url, data=payload, timeout=30)
                data = resp.json()
            except requests.RequestException as exc:
                if attempt == max_retries:
                    raise InstagramGraphAPIError(f"Network error creating item container for {image_url}: {exc}")
                sleep_time = base_delay * (2 ** (attempt - 1))
                print(f"    [!] Network error on attempt {attempt}/{max_retries}: {exc}. Retrying in {sleep_time:.1f}s...")
                time.sleep(sleep_time)
                continue

            if resp.status_code == 200 and "id" in data:
                cid = data["id"]
                cache[image_url] = {"id": cid, "timestamp": time.time()}
                self._save_containers_cache(cache)
                return cid

            error_obj = data.get("error", {})
            error_msg = error_obj.get("message", resp.text)
            error_subcode = error_obj.get("error_subcode")
            user_msg = error_obj.get("error_user_msg") or error_obj.get("error_user_title")

            # Hard limit: Daily Instagram publishing cap reached (subcode 2207042)
            if error_subcode == 2207042:
                raise InstagramGraphAPIError(f"Daily Instagram publishing limit reached (100 posts/24h): {error_msg}")

            # Transient / retryable conditions:
            # - 2207052: "The media could not be fetched from this URI" / "Only photo or video can be accepted as media type"
            # - 2207001: Internal Instagram server error
            # - 2207003: Timeout downloading media
            # - HTTP 429: Rate limited
            # - HTTP 5xx: Server errors
            # - "Only photo or video can be accepted" in message
            is_retryable = (
                resp.status_code in (429, 500, 502, 503, 504)
                or error_subcode in (2207052, 2207001, 2207003)
                or "Only photo or video can be accepted" in str(error_msg)
            )

            detailed_msg = error_msg
            if error_subcode:
                detailed_msg += f" (subcode {error_subcode})"
            if user_msg:
                detailed_msg += f": {user_msg}"

            last_error = detailed_msg

            if is_retryable and attempt < max_retries:
                sleep_time = base_delay * (2 ** (attempt - 1))
                print(f"    [!] Transient media fetch error on attempt {attempt}/{max_retries}: {error_msg}. Retrying in {sleep_time:.1f}s...")
                time.sleep(sleep_time)
                continue

            raise InstagramGraphAPIError(f"Failed to create item container for {image_url}: {detailed_msg}")

        raise InstagramGraphAPIError(f"Failed to create item container for {image_url}: {last_error}")

    def wait_for_container(
        self,
        container_id: str,
        timeout: int = DEFAULT_POLL_TIMEOUT_SECS,
        interval: int = DEFAULT_POLL_INTERVAL_SECS
    ) -> bool:
        """
        Polls a media container until its status is FINISHED or times out.
        
        Returns:
            True if FINISHED.
        Raises:
            InstagramGraphAPIError if status is ERROR or polling times out.
        """
        url = f"{GRAPH_API_BASE}/{container_id}"
        params = {
            "fields": "status_code,status",
            "access_token": self.access_token
        }

        start_time = time.time()
        while time.time() - start_time < timeout:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            status_code = data.get("status_code")

            if status_code == "FINISHED":
                return True
            elif status_code == "ERROR":
                err = data.get("status", "Unknown container error")
                raise InstagramGraphAPIError(f"Container {container_id} processing failed: {err}")
            elif status_code in ("IN_PROGRESS", None):
                time.sleep(interval)
            else:
                time.sleep(interval)

        raise InstagramGraphAPIError(f"Timed out waiting for container {container_id} after {timeout}s.")

    def create_carousel_container(
        self,
        child_container_ids: list[str],
        caption: str,
        max_retries: int = 3,
        base_delay: float = 2.0
    ) -> str:
        """
        Creates a parent carousel container linking the child containers.
        
        Args:
            child_container_ids: List of container IDs (2 to 10 items).
            caption: Root carousel post caption.
            max_retries: Maximum retry attempts for transient server errors.
            base_delay: Initial sleep seconds between retries.
            
        Returns:
            Carousel container ID string.
        """
        if not (2 <= len(child_container_ids) <= 10):
            raise ValueError(f"Instagram carousels require between 2 and 10 items. Got {len(child_container_ids)}.")

        url = f"{GRAPH_API_BASE}/{self.instagram_account_id}/media"
        payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(child_container_ids),
            "caption": caption,
            "access_token": self.access_token
        }

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(url, data=payload, timeout=30)
                data = resp.json()
            except requests.RequestException as exc:
                if attempt == max_retries:
                    raise InstagramGraphAPIError(f"Network error creating carousel container: {exc}")
                time.sleep(base_delay * (2 ** (attempt - 1)))
                continue

            if resp.status_code == 200 and "id" in data:
                return data["id"]

            error_obj = data.get("error", {})
            error_msg = error_obj.get("message", resp.text)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
                continue

            raise InstagramGraphAPIError(f"Failed to create carousel container: {error_msg}")

    def publish_carousel(
        self,
        creation_id: str,
        max_retries: int = 3,
        base_delay: float = 2.0
    ) -> dict[str, Any]:
        """
        Publishes the prepared carousel container to Instagram.
        
        Args:
            creation_id: Carousel container ID.
            max_retries: Maximum retry attempts for transient server errors.
            base_delay: Initial sleep seconds between retries.
            
        Returns:
            Dictionary with media_id and permalink.
        """
        url = f"{GRAPH_API_BASE}/{self.instagram_account_id}/media_publish"
        payload = {
            "creation_id": creation_id,
            "access_token": self.access_token
        }

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(url, data=payload, timeout=30)
                data = resp.json()
            except requests.RequestException as exc:
                if attempt == max_retries:
                    raise InstagramGraphAPIError(f"Network error publishing carousel: {exc}")
                time.sleep(base_delay * (2 ** (attempt - 1)))
                continue

            if resp.status_code == 200 and "id" in data:
                media_id = data["id"]
                permalink = self.get_media_permalink(media_id)
                return {
                    "media_id": media_id,
                    "permalink": permalink
                }

            error_obj = data.get("error", {})
            error_msg = error_obj.get("message", resp.text)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
                continue

            raise InstagramGraphAPIError(f"Failed to publish carousel: {error_msg}")

    def get_media_permalink(self, media_id: str, retries: int = 3, delay: int = 2) -> Optional[str]:
        """Fetches the live public permalink (post URL) for a published Instagram post."""
        url = f"{GRAPH_API_BASE}/{media_id}"
        params = {
            "fields": "permalink",
            "access_token": self.access_token
        }
        for attempt in range(retries):
            try:
                resp = requests.get(url, params=params, timeout=10)
                data = resp.json()
                permalink = data.get("permalink")
                if permalink:
                    return str(permalink)
            except Exception:
                pass
            if attempt < retries - 1:
                time.sleep(delay)
        return None

    def post_carousel(
        self,
        image_urls: list[str],
        caption: str,
        inter_item_delay: float = 1.0
    ) -> dict[str, Any]:
        """
        High-level helper executing the full end-to-end carousel publishing flow.
        
        1. Creates child item containers for each image URL.
        2. Waits for all child items to reach FINISHED status.
        3. Creates the parent carousel container with caption.
        4. Waits for the carousel container to reach FINISHED status.
        5. Publishes the carousel to Instagram.
        """
        if not (2 <= len(image_urls) <= 10):
            raise ValueError(f"Carousel requires 2-10 image URLs, got {len(image_urls)}")

        print(f"[*] Creating {len(image_urls)} child containers on Instagram...")
        child_ids = []
        for idx, img_url in enumerate(image_urls, start=1):
            print(f"    -> Uploading slide {idx}/{len(image_urls)}: {img_url[:70]}...")
            cid = self.create_item_container(img_url)
            child_ids.append(cid)
            if idx < len(image_urls) and inter_item_delay > 0:
                time.sleep(inter_item_delay)

        print("[*] Verifying child containers processing status...")
        for cid in child_ids:
            self.wait_for_container(cid)

        print("[*] Creating parent carousel container...")
        carousel_id = self.create_carousel_container(child_ids, caption)
        self.wait_for_container(carousel_id)

        print("[*] Publishing carousel post...")
        result = self.publish_carousel(carousel_id)
        print(f"[+] Successfully published to Instagram! Media ID: {result['media_id']}")
        if result.get("permalink"):
            print(f"    Permalink: {result['permalink']}")
        return result

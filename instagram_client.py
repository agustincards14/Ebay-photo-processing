"""Instagram Graph API Client.

Implements the official Meta Instagram Content Publishing endpoints for creating
and publishing multi-item Carousel posts.
"""

import time
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

    def __init__(self, access_token: str, instagram_account_id: str):
        if not access_token or not instagram_account_id:
            raise ValueError("Both access_token and instagram_account_id are required.")
        self.access_token = access_token
        self.instagram_account_id = instagram_account_id

    def create_item_container(self, image_url: str) -> str:
        """
        Creates an individual child media container for a carousel item.
        
        Args:
            image_url: Publicly accessible HTTPS URL of the image.
            
        Returns:
            Container ID string.
        """
        url = f"{GRAPH_API_BASE}/{self.instagram_account_id}/media"
        payload = {
            "image_url": image_url,
            "is_carousel_item": "true",
            "access_token": self.access_token
        }

        resp = requests.post(url, data=payload, timeout=20)
        data = resp.json()

        if resp.status_code != 200 or "id" not in data:
            error_msg = data.get("error", {}).get("message", resp.text)
            raise InstagramGraphAPIError(f"Failed to create item container for {image_url}: {error_msg}")

        return data["id"]

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

    def create_carousel_container(self, child_container_ids: list[str], caption: str) -> str:
        """
        Creates a parent carousel container linking the child containers.
        
        Args:
            child_container_ids: List of container IDs (2 to 10 items).
            caption: Root carousel post caption.
            
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

        resp = requests.post(url, data=payload, timeout=20)
        data = resp.json()

        if resp.status_code != 200 or "id" not in data:
            error_msg = data.get("error", {}).get("message", resp.text)
            raise InstagramGraphAPIError(f"Failed to create carousel container: {error_msg}")

        return data["id"]

    def publish_carousel(self, creation_id: str) -> dict[str, Any]:
        """
        Publishes the prepared carousel container to Instagram.
        
        Args:
            creation_id: Carousel container ID.
            
        Returns:
            Dictionary with media_id and permalink.
        """
        url = f"{GRAPH_API_BASE}/{self.instagram_account_id}/media_publish"
        payload = {
            "creation_id": creation_id,
            "access_token": self.access_token
        }

        resp = requests.post(url, data=payload, timeout=20)
        data = resp.json()

        if resp.status_code != 200 or "id" not in data:
            error_msg = data.get("error", {}).get("message", resp.text)
            raise InstagramGraphAPIError(f"Failed to publish carousel: {error_msg}")

        media_id = data["id"]

        # Fetch permalink
        permalink = self.get_media_permalink(media_id)
        return {
            "media_id": media_id,
            "permalink": permalink
        }

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

    def post_carousel(self, image_urls: list[str], caption: str) -> dict[str, Any]:
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

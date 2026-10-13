"""Instagram OAuth & Credential Management Helper.

Handles loading, validating, and refreshing access tokens for the Meta Instagram Graph API.
Credentials can be stored in `instagram_credentials.json` or supplied via environment variables:
  - INSTAGRAM_ACCESS_TOKEN
  - INSTAGRAM_ACCOUNT_ID
  - INSTAGRAM_APP_ID
  - INSTAGRAM_APP_SECRET
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Any, Optional
import requests

CREDENTIALS_FILE = Path(__file__).resolve().parent / "instagram_credentials.json"
GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def load_credentials(account_name: Optional[str] = None) -> dict[str, Any]:
    """Loads Instagram credentials from file or environment variables."""
    creds = {
        "app_id": os.getenv("INSTAGRAM_APP_ID", ""),
        "app_secret": os.getenv("INSTAGRAM_APP_SECRET", ""),
        "instagram_account_id": os.getenv("INSTAGRAM_ACCOUNT_ID", ""),
        "access_token": os.getenv("INSTAGRAM_ACCESS_TOKEN", ""),
    }

    if CREDENTIALS_FILE.exists():
        try:
            with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Support both flat and multi-account configurations
            if "accounts" in data:
                acc_key = account_name or data.get("default_account")
                if not acc_key or acc_key not in data["accounts"]:
                    acc_key = next(iter(data["accounts"])) if data["accounts"] else None
                if acc_key and acc_key in data["accounts"]:
                    file_creds = data["accounts"][acc_key]
                    creds.update({k: v for k, v in file_creds.items() if v})
            else:
                creds.update({k: v for k, v in data.items() if v})
        except Exception as e:
            print(f"[!] Warning: Could not read {CREDENTIALS_FILE}: {e}", file=sys.stderr)

    return creds


def verify_credentials(access_token: str, instagram_account_id: str) -> dict[str, Any]:
    """Verifies that the access token and Instagram account ID are active and reachable."""
    if not access_token or not instagram_account_id:
        return {"success": False, "error": "Missing access_token or instagram_account_id"}

    url = f"{GRAPH_API_BASE}/{instagram_account_id}"
    params = {
        "fields": "id,username,name",
        "access_token": access_token
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if resp.status_code == 200 and "id" in data:
            return {"success": True, "account": data}
        error_msg = data.get("error", {}).get("message", resp.text)
        return {"success": False, "error": f"API Error ({resp.status_code}): {error_msg}"}
    except Exception as e:
        return {"success": False, "error": f"Network exception: {e}"}


def exchange_for_long_lived_token(app_id: str, app_secret: str, short_lived_token: str) -> dict[str, Any]:
    """Exchanges a short-lived user access token for a 60-day long-lived token."""
    url = f"{GRAPH_API_BASE}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_lived_token
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if resp.status_code == 200 and "access_token" in data:
            return {"success": True, "access_token": data["access_token"], "expires_in": data.get("expires_in")}
        return {"success": False, "error": data.get("error", {}).get("message", resp.text)}
    except Exception as e:
        return {"success": False, "error": f"Network exception: {e}"}


def main():
    parser = argparse.ArgumentParser(description="Instagram OAuth Credential Helper")
    parser.add_argument("--verify", action="store_true", help="Verify loaded credentials against Instagram Graph API")
    parser.add_argument("--account", type=str, default=None, help="Target account name from credentials file")
    parser.add_argument("--exchange", type=str, help="Exchange a short-lived token for a long-lived 60-day token")
    args = parser.parse_args()

    creds = load_credentials(account_name=args.account)

    if args.exchange:
        if not creds.get("app_id") or not creds.get("app_secret"):
            print("[-] Error: app_id and app_secret required to exchange token.", file=sys.stderr)
            sys.exit(1)
        result = exchange_for_long_lived_token(creds["app_id"], creds["app_secret"], args.exchange)
        if result["success"]:
            print(f"[+] Long-lived access token acquired:\n{result['access_token']}")
            if result.get("expires_in"):
                print(f"    Expires in: {result['expires_in']} seconds (~{result['expires_in'] // 86400} days)")
        else:
            print(f"[-] Exchange failed: {result['error']}", file=sys.stderr)
            sys.exit(1)
        return

    if args.verify or len(sys.argv) == 1:
        token = creds.get("access_token")
        account_id = creds.get("instagram_account_id")
        print(f"[*] Account ID: {account_id or '(not set)'}")
        print(f"[*] Token: {'*' * 8 + token[-4:] if token and len(token) > 4 else '(not set)'}")
        
        result = verify_credentials(token or "", account_id or "")
        if result["success"]:
            acc = result["account"]
            print(f"[+] Credentials valid! Connected to @{acc.get('username')} ({acc.get('name')})")
        else:
            print(f"[-] Verification failed: {result.get('error')}")
            if not CREDENTIALS_FILE.exists():
                print(f"    Hint: Copy instagram_credentials.json.example to instagram_credentials.json and fill in credentials.")
            sys.exit(1)


if __name__ == "__main__":
    main()

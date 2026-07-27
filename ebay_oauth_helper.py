import os
import json
import base64
import time
import sys
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote, unquote
from urllib.error import HTTPError, URLError

CONFIG_FILE = "ebay_credentials.json"
DEFAULT_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.account"

ENVIRONMENTS = {
    "sandbox": {
        "auth_url": "https://auth.sandbox.ebay.com/oauth2/authorize",
        "token_url": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
        "api_host": "https://api.sandbox.ebay.com"
    },
    "production": {
        "auth_url": "https://auth.ebay.com/oauth2/authorize",
        "token_url": "https://api.ebay.com/identity/v1/oauth2/token",
        "api_host": "https://api.ebay.com"
    }
}

DEFAULT_ENV_STRUCTURE = {
    "sandbox": {
        "client_id": "",
        "client_secret": "",
        "ru_name": "",
        "access_token": "",
        "refresh_token": "",
        "expires_at": 0
    },
    "production": {
        "client_id": "",
        "client_secret": "",
        "ru_name": "",
        "access_token": "",
        "refresh_token": "",
        "expires_at": 0
    }
}

STORES = ["photo_vault", "personal"]

def normalize_store_name(store_input: str) -> str:
    if not store_input:
        return "photo_vault"
    s = store_input.strip().lower().replace(" ", "_").replace("-", "_")
    if "personal" in s:
        return "personal"
    if "photo" in s or "vault" in s:
        return "photo_vault"
    return s

def load_credentials():
    """Load credentials from the config file with auto-migration to store-based structure."""
    default_structure = {
        "photo_vault": json.loads(json.dumps(DEFAULT_ENV_STRUCTURE)),
        "personal": json.loads(json.dumps(DEFAULT_ENV_STRUCTURE))
    }
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                
            if isinstance(data, dict):
                # Migration check 1: if flat legacy structure
                if "client_id" in data:
                    print("[*] Migrating legacy flat credentials to store-based structure ('personal')...", file=sys.stderr)
                    default_structure["personal"]["sandbox"]["client_id"] = data.get("client_id", "")
                    default_structure["personal"]["sandbox"]["client_secret"] = data.get("client_secret", "")
                    default_structure["personal"]["sandbox"]["ru_name"] = data.get("ru_name", "")
                    default_structure["personal"]["sandbox"]["access_token"] = data.get("access_token", "")
                    default_structure["personal"]["sandbox"]["refresh_token"] = data.get("refresh_token", "")
                    default_structure["personal"]["sandbox"]["expires_at"] = data.get("expires_at", 0)
                    save_credentials(default_structure)
                    return default_structure
                
                # Migration check 2: top-level has "sandbox" or "production" but not "photo_vault" / "personal"
                if ("sandbox" in data or "production" in data) and not ("photo_vault" in data or "personal" in data):
                    print("[*] Migrating environment credentials to store-based structure ('personal')...", file=sys.stderr)
                    if "sandbox" in data and isinstance(data["sandbox"], dict):
                        default_structure["personal"]["sandbox"].update(data["sandbox"])
                    if "production" in data and isinstance(data["production"], dict):
                        default_structure["personal"]["production"].update(data["production"])
                    save_credentials(default_structure)
                    return default_structure
                
                # Ensure all expected store keys and environment subkeys exist
                for store in STORES:
                    if store not in data or not isinstance(data[store], dict):
                        data[store] = json.loads(json.dumps(DEFAULT_ENV_STRUCTURE))
                    else:
                        for env in ["sandbox", "production"]:
                            if env not in data[store] or not isinstance(data[store][env], dict):
                                data[store][env] = json.loads(json.dumps(DEFAULT_ENV_STRUCTURE[env]))
                return data
        except Exception as e:
            print(f"[!] Error loading credentials: {e}", file=sys.stderr)
            
    return default_structure

def save_credentials(creds):
    """Save credentials to the config file."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(creds, f, indent=4)
    print(f"\n[+] Credentials saved to '{CONFIG_FILE}'.", file=sys.stderr)

def get_auth_url(client_id, ru_name, env="sandbox", scope=DEFAULT_SCOPE):
    """Generate the user authorization URL for the specified environment."""
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": ru_name,
        "scope": scope
    }
    encoded_params = urlencode(params)
    base_auth_url = ENVIRONMENTS.get(env, ENVIRONMENTS["sandbox"])["auth_url"]
    return f"{base_auth_url}?{encoded_params}"

def exchange_code_for_token(client_id, client_secret, ru_name, auth_code, env="sandbox"):
    """Exchange the authorization code for access and refresh tokens."""
    url = ENVIRONMENTS.get(env, ENVIRONMENTS["sandbox"])["token_url"]
    
    auth_str = f"{client_id}:{client_secret}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {b64_auth}"
    }
    
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": ru_name
    }
    encoded_data = urlencode(data).encode("utf-8")
    
    req = Request(url, data=encoded_data, headers=headers, method="POST")
    try:
        with urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data
    except HTTPError as e:
        print(f"\n[!] Error exchanging authorization code ({env}): {e}", file=sys.stderr)
        try:
            err_body = e.read().decode("utf-8")
            print(f"Server response: {err_body}", file=sys.stderr)
        except Exception:
            pass
        return None
    except URLError as e:
        print(f"\n[!] Network error exchanging authorization code ({env}): {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"\n[!] Unexpected error exchanging authorization code ({env}): {e}", file=sys.stderr)
        return None

def refresh_access_token(client_id, client_secret, refresh_token, env="sandbox", scope=None):
    """Refresh an expired User Access Token."""
    url = ENVIRONMENTS.get(env, ENVIRONMENTS["sandbox"])["token_url"]
    
    auth_str = f"{client_id}:{client_secret}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {b64_auth}"
    }
    
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    if scope:
        data["scope"] = scope
    encoded_data = urlencode(data).encode("utf-8")
    
    req = Request(url, data=encoded_data, headers=headers, method="POST")
    try:
        with urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data
    except HTTPError as e:
        print(f"\n[!] Error refreshing access token ({env}): {e}", file=sys.stderr)
        try:
            err_body = e.read().decode("utf-8")
            print(f"Server response: {err_body}", file=sys.stderr)
        except Exception:
            pass
        return None
    except URLError as e:
        print(f"\n[!] Network error refreshing access token ({env}): {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"\n[!] Unexpected error refreshing access token ({env}): {e}", file=sys.stderr)
        return None

def check_and_get_token(store=None, env=None):
    """Helper function to load token and refresh it if expired for specified store and environment."""
    if store is None:
        store = os.environ.get("EBAY_STORE", "photo_vault")
    store = normalize_store_name(store)
    
    if env is None:
        env = os.environ.get("EBAY_ENV", "sandbox").lower()
        if env not in ENVIRONMENTS:
            env = "sandbox"
            
    creds_dict = load_credentials()
    store_creds = creds_dict.get(store, {})
    creds = store_creds.get(env, {})
    
    if not creds or not creds.get("client_id"):
        print(f"[-] No credentials found for store '{store}' ({env}). Run 'python ebay_oauth_helper.py --store {store} --env {env}' to authenticate first.", file=sys.stderr)
        return None
        
    expires_at = creds.get("expires_at", 0)
    current_time = time.time()
    
    if current_time + 300 < expires_at:
        return creds.get("access_token")
        
    print(f"[*] Access token for store '{store}' ({env}) expired or expiring soon. Refreshing...", file=sys.stderr)
    
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    refresh_token = creds.get("refresh_token")
    
    if not (client_id and client_secret and refresh_token):
        print(f"[-] Missing credentials required for token refresh on store '{store}' ({env}). Re-authenticate.", file=sys.stderr)
        return None
        
    res = refresh_access_token(client_id, client_secret, refresh_token, env=env)
    if res:
        creds["access_token"] = res["access_token"]
        creds["expires_at"] = time.time() + int(res["expires_in"])
        creds_dict[store][env] = creds
        save_credentials(creds_dict)
        print(f"[+] Access token for store '{store}' ({env}) successfully refreshed!", file=sys.stderr)
        return creds["access_token"]
    else:
        print(f"[-] Failed to refresh token for store '{store}' ({env}).", file=sys.stderr)
        return None

def interactive_login(store=None, env=None):
    print("=" * 60)
    print("            eBay API Authorization Code Flow Helper")
    print("=" * 60)
    
    creds_dict = load_credentials()
    
    if store is None:
        print("\nSelect eBay Store Account:")
        print("1. Photo Vault (Default)")
        print("2. Personal")
        choice = input("Enter choice [1]: ").strip()
        if choice == "2":
            store = "personal"
        else:
            store = "photo_vault"
    store = normalize_store_name(store)
    
    if env is None:
        print("\nSelect Environment:")
        print("1. Sandbox (SBX)")
        print("2. Production (PRD)")
        choice = input("Enter choice [1]: ").strip()
        if choice == "2":
            env = "production"
        else:
            env = "sandbox"
            
    print(f"\n---> Running authentication for Store: {store.upper()} | Environment: {env.upper()} <---\n")
    
    store_creds = creds_dict.get(store, {})
    creds = store_creds.get(env, {})
    
    client_id = input(f"Enter App ID (Client ID) [{creds.get('client_id', '')}]: ").strip() or creds.get('client_id', '')
    client_secret = input(f"Enter Cert ID (Client Secret) [{creds.get('client_secret', '')}]: ").strip() or creds.get('client_secret', '')
    ru_name = input(f"Enter RuName (Redirect URI Name) [{creds.get('ru_name', '')}]: ").strip() or creds.get('ru_name', '')
    
    if not (client_id and client_secret and ru_name):
        print("[!] Client ID, Client Secret, and RuName are required.")
        return

    creds_draft = {
        "client_id": client_id,
        "client_secret": client_secret,
        "ru_name": ru_name,
        "access_token": creds.get("access_token", ""),
        "refresh_token": creds.get("refresh_token", ""),
        "expires_at": creds.get("expires_at", 0)
    }
    
    auth_url = get_auth_url(client_id, ru_name, env=env)
    
    print("\n" + "-" * 50)
    if env == "sandbox":
        print("1. Open the following URL in your browser and sign in using your")
        print("   eBay Sandbox Test User credentials (e.g. TESTUSER_xxxx):")
    else:
        print(f"1. Open the following URL in your browser and sign in using your")
        print(f"   {store.upper()} production eBay Seller Account credentials:")
    print("-" * 50)
    print(auth_url)
    print("-" * 50)
    
    print("\n2. After signing in and clicking 'Agree', you will be redirected to a URL.")
    print("   Copy the entire redirected URL or just the value of the 'code' query parameter.")
    
    redirect_input = input("\nPaste the redirected URL or code here: ").strip()
    if not redirect_input:
        print("[!] Input cannot be empty.")
        return
        
    code = redirect_input
    if "code=" in redirect_input:
        try:
            parts = redirect_input.split("code=")
            code = parts[1].split("&")[0]
        except Exception:
            pass
            
    code = unquote(code)
            
    print(f"\n[*] Exchanging authorization code for store '{store}' ({env.upper()}) User Access Token...")
    res = exchange_code_for_token(client_id, client_secret, ru_name, code, env=env)
    
    if res:
        creds_draft["access_token"] = res["access_token"]
        creds_draft["refresh_token"] = res["refresh_token"]
        creds_draft["expires_at"] = time.time() + int(res["expires_in"])
        
        if store not in creds_dict:
            creds_dict[store] = json.loads(json.dumps(DEFAULT_ENV_STRUCTURE))
        creds_dict[store][env] = creds_draft
        save_credentials(creds_dict)
        
        print(f"\n[+] SUCCESS! Token retrieved successfully for store '{store}' ({env.upper()}).")
        print(f"    Access Token (Expires in {res['expires_in']}s): {res['access_token'][:30]}...")
        print(f"    Refresh Token: {res['refresh_token'][:30]}...")
    else:
        print(f"\n[-] FAILED to retrieve tokens for store '{store}'. Check credentials and retry.")

if __name__ == "__main__":
    get_token_mode = False
    env = None
    store = None
    
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--token", "-t"):
            get_token_mode = True
        elif arg in ("--env", "-e"):
            if i + 1 < len(args):
                env = args[i+1].lower()
                i += 1
            else:
                print("[-] Error: --env / -e option requires an argument.", file=sys.stderr)
                sys.exit(1)
        elif arg in ("--store", "-s"):
            if i + 1 < len(args):
                store = args[i+1].lower()
                i += 1
            else:
                print("[-] Error: --store / -s option requires an argument.", file=sys.stderr)
                sys.exit(1)
        i += 1
        
    if env and env not in ENVIRONMENTS:
        print(f"[-] Error: Unsupported environment '{env}'. Choose 'sandbox' or 'production'.", file=sys.stderr)
        sys.exit(1)
        
    if get_token_mode:
        if not store:
            store = os.environ.get("EBAY_STORE", "photo_vault")
        if not env:
            env = os.environ.get("EBAY_ENV", "sandbox").lower()
            if env not in ENVIRONMENTS:
                env = "sandbox"
        token = check_and_get_token(store=store, env=env)
        if token:
            print(token)
            sys.exit(0)
        else:
            sys.exit(1)
    else:
        interactive_login(store=store, env=env)

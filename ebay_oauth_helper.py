import os
import json
import base64
import time
import sys
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote, unquote


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

def load_credentials():
    """Load credentials from the config file if it exists."""
    default_structure = {
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
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                
            # Migration check: if top-level has client_id, migrate it to "sandbox"
            if isinstance(data, dict) and "client_id" in data:
                print("[*] Migrating old flat credentials format to nested environment format...", file=sys.stderr)
                migrated_data = {
                    "sandbox": {
                        "client_id": data.get("client_id", ""),
                        "client_secret": data.get("client_secret", ""),
                        "ru_name": data.get("ru_name", ""),
                        "access_token": data.get("access_token", ""),
                        "refresh_token": data.get("refresh_token", ""),
                        "expires_at": data.get("expires_at", 0)
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
                save_credentials(migrated_data)
                return migrated_data
            
            # Ensure both keys exist
            if isinstance(data, dict):
                for env in ["sandbox", "production"]:
                    if env not in data or not isinstance(data[env], dict):
                        data[env] = default_structure[env]
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
    
    # eBay requires basic auth for client credentials
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
    except Exception as e:
        print(f"\n[!] Error exchanging authorization code ({env}): {e}", file=sys.stderr)
        # Try to read error body if available
        if hasattr(e, 'read'):
            try:
                err_body = e.read().decode("utf-8")
                print(f"Server response: {err_body}", file=sys.stderr)
            except Exception:
                pass
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
    except Exception as e:
        print(f"\n[!] Error refreshing access token ({env}): {e}", file=sys.stderr)
        if hasattr(e, 'read'):
            try:
                err_body = e.read().decode("utf-8")
                print(f"Server response: {err_body}", file=sys.stderr)
            except Exception:
                pass
        return None

def check_and_get_token(env=None):
    """Helper function to load token and refresh it if expired for the specified environment."""
    if env is None:
        env = os.environ.get("EBAY_ENV", "sandbox").lower()
        if env not in ENVIRONMENTS:
            env = "sandbox"
            
    creds_dict = load_credentials()
    creds = creds_dict.get(env, {})
    
    if not creds or not creds.get("client_id"):
        print(f"[-] No credentials found for environment '{env}'. Run 'python ebay_oauth_helper.py --env {env}' to authenticate first.", file=sys.stderr)
        return None
        
    # Check if access token is expired (using a safety buffer of 5 minutes)
    expires_at = creds.get("expires_at", 0)
    current_time = time.time()
    
    if current_time + 300 < expires_at:
        return creds.get("access_token")
        
    print(f"[*] Access token for '{env}' expired or expiring soon. Refreshing...", file=sys.stderr)
    
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    refresh_token = creds.get("refresh_token")
    
    if not (client_id and client_secret and refresh_token):
        print(f"[-] Missing credentials required for token refresh on environment '{env}'. Re-authenticate.", file=sys.stderr)
        return None
        
    res = refresh_access_token(client_id, client_secret, refresh_token, env=env)
    if res:
        creds["access_token"] = res["access_token"]
        creds["expires_at"] = time.time() + int(res["expires_in"])
        creds_dict[env] = creds
        save_credentials(creds_dict)
        print(f"[+] Access token for '{env}' successfully refreshed!", file=sys.stderr)
        return creds["access_token"]
    else:
        print(f"[-] Failed to refresh token for environment '{env}'.", file=sys.stderr)
        return None

def interactive_login(env=None):
    print("=" * 60)
    print("            eBay API Authorization Code Flow Helper")
    print("=" * 60)
    
    creds_dict = load_credentials()
    
    if env is None:
        print("Select the environment:")
        print("1. Sandbox (SBX)")
        print("2. Production (PRD)")
        choice = input("Enter choice [1]: ").strip()
        if choice == "2":
            env = "production"
        else:
            env = "sandbox"
            
    print(f"\n---> Running authentication for environment: {env.upper()} <---\n")
    
    creds = creds_dict.get(env, {})
    
    client_id = input(f"Enter App ID (Client ID) [{creds.get('client_id', '')}]: ").strip() or creds.get('client_id', '')
    client_secret = input(f"Enter Cert ID (Client Secret) [{creds.get('client_secret', '')}]: ").strip() or creds.get('client_secret', '')
    ru_name = input(f"Enter RuName (Redirect URI Name) [{creds.get('ru_name', '')}]: ").strip() or creds.get('ru_name', '')
    
    if not (client_id and client_secret and ru_name):
        print("[!] Client ID, Client Secret, and RuName are required.")
        return

    # Keep credentials draft
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
        print("1. Open the following URL in your browser and sign in using your")
        print("   production eBay Seller Account credentials:")
    print("-" * 50)
    print(auth_url)
    print("-" * 50)
    
    print("\n2. After signing in and clicking 'Agree', you will be redirected to a URL.")
    print("   Copy the entire redirected URL or just the value of the 'code' query parameter.")
    
    redirect_input = input("\nPaste the redirected URL or code here: ").strip()
    if not redirect_input:
        print("[!] Input cannot be empty.")
        return
        
    # Extract code parameter if they pasted the whole URL
    code = redirect_input
    if "code=" in redirect_input:
        try:
            parts = redirect_input.split("code=")
            code = parts[1].split("&")[0]
        except Exception:
            pass
            
    # Decode the authorization code to prevent double-URL-encoding
    code = unquote(code)
            
    print(f"\n[*] Exchanging authorization code for {env.upper()} User Access Token...")
    res = exchange_code_for_token(client_id, client_secret, ru_name, code, env=env)
    
    if res:
        creds_draft["access_token"] = res["access_token"]
        creds_draft["refresh_token"] = res["refresh_token"]
        creds_draft["expires_at"] = time.time() + int(res["expires_in"])
        
        creds_dict[env] = creds_draft
        save_credentials(creds_dict)
        
        print(f"\n[+] SUCCESS! Token retrieved successfully for {env.upper()}.")
        print(f"    Access Token (Expires in {res['expires_in']}s): {res['access_token'][:30]}...")
        print(f"    Refresh Token: {res['refresh_token'][:30]}...")
    else:
        print("\n[-] FAILED to retrieve tokens. Check credentials and retry.")

if __name__ == "__main__":
    # Parse arguments
    get_token_mode = False
    env = None
    
    # Process sys.argv
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
        i += 1
        
    if env and env not in ENVIRONMENTS:
        print(f"[-] Error: Unsupported environment '{env}'. Choose 'sandbox' or 'production'.", file=sys.stderr)
        sys.exit(1)
        
    if get_token_mode:
        if not env:
            env = os.environ.get("EBAY_ENV", "sandbox").lower()
            if env not in ENVIRONMENTS:
                env = "sandbox"
        token = check_and_get_token(env=env)
        if token:
            print(token)
            sys.exit(0)
        else:
            sys.exit(1)
    else:
        interactive_login(env=env)

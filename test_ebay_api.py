import os
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS

def test_api():
    env = os.environ.get("EBAY_ENV", "sandbox").lower()
    if env not in ENVIRONMENTS:
        env = "sandbox"
        
    api_host = ENVIRONMENTS[env]["api_host"]
    env_label = env.upper()
    
    print("=" * 60)
    print(f"           eBay {env_label} Sell Inventory API Tester")
    print("=" * 60)
    
    # 1. Retrieve the token (auto-refreshes if needed)
    access_token = check_and_get_token(env=env)
    if not access_token:
        print(f"[-] Verification failed: Could not retrieve a valid access token for {env_label}.")
        return
        
    print(f"[+] Successfully loaded {env_label} User Access Token.")
    
    headers = {
        "Content-Type": "application/json",
        "Content-Language": "en-US",
        "Authorization": f"Bearer {access_token}"
    }
    
    # 2. Test Create Location (STORE_MAIN)
    print("\n[*] Testing connection: Creating location 'STORE_MAIN' (POST /location/STORE_MAIN)...")
    url_create_loc = f"{api_host}/sell/inventory/v1/location/STORE_MAIN"
    location_payload = {
        "name": "Main Store Location",
        "locationTypes": ["WAREHOUSE"],
        "merchantLocationStatus": "ENABLED",
        "location": {
            "address": {
                "addressLine1": "2055 Hamilton Ave",
                "city": "San Jose",
                "stateOrProvince": "CA",
                "postalCode": "95125",
                "country": "US"
            }
        }
    }
    encoded_payload = json.dumps(location_payload).encode("utf-8")
    req_create_loc = Request(url_create_loc, data=encoded_payload, headers=headers, method="POST")
    
    try:
        with urlopen(req_create_loc) as response:
            print(f"[+] Location creation successful! Status Code: {response.status}")
    except HTTPError as e:
        is_already_exists = False
        try:
            err_body_str = e.read().decode("utf-8")
            if err_body_str:
                err_body = json.loads(err_body_str)
                if any("already exists" in err.get("message", "") for err in err_body.get("errors", [])):
                    print("[+] Location 'STORE_MAIN' already exists and is ready for use.")
                    is_already_exists = True
        except Exception:
            err_body_str = ""
            
        if not is_already_exists:
            print(f"[-] HTTP Error creating location: {e.code} {e.reason}")
            if err_body_str:
                print(f"Error details: {err_body_str}")
    except Exception as e:
        print(f"[-] Error creating location: {e}")
        
    # 3. Test Create Inventory Item (PUT /inventory_item/{sku})
    print("\n[*] Testing connection: Creating test inventory item 'test-sku-001' (PUT /inventory_item/test-sku-001)...")
    url_put_item = f"{api_host}/sell/inventory/v1/inventory_item/test-sku-001"
    item_payload = {
        "availability": {
            "shipToLocationAvailability": {
                "quantity": 1
            }
        },
        "condition": "USED_VERY_GOOD",
        "conditionDescription": "Vintage test photo in great condition.",
        "product": {
            "title": "Test Photo of Bridge, c. 1950",
            "description": "A vintage test photograph used for API validation.",
            "aspects": {
                "Subject": ["Bridge"],
                "Year of Production": ["1950"],
                "Size": ["4x6in"],
                "Type": ["Photograph"]
            },
            "imageUrls": [
                "https://picsum.photos/id/43/800/600"
            ]
        }
    }
    encoded_item_payload = json.dumps(item_payload).encode("utf-8")
    req_put_item = Request(url_put_item, data=encoded_item_payload, headers=headers, method="PUT")
    
    try:
        with urlopen(req_put_item) as response:
            print(f"[+] Inventory item created successfully! Status Code: {response.status}")
    except HTTPError as e:
        print(f"[-] HTTP Error creating item: {e.code} {e.reason}")
        try:
            print(f"Error details: {e.read().decode('utf-8')}")
        except Exception:
            pass
        return
    except Exception as e:
        print(f"[-] Error creating item: {e}")
        return

    # 4. Test Retrieve Inventory Item (GET /inventory_item/{sku})
    print("\n[*] Testing connection: Retrieving test item details (GET /inventory_item/test-sku-001)...")
    req_get_single = Request(url_put_item, headers=headers, method="GET")
    
    try:
        with urlopen(req_get_single) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            print("[+] Retrieved item successfully!")
            print(f"Title: {res_data.get('product', {}).get('title')}")
            print(f"Condition: {res_data.get('condition')}")
    except HTTPError as e:
        print(f"[-] HTTP Error retrieving item: {e.code} {e.reason}")
    except Exception as e:
        print(f"[-] Error: {e}")

    print("\n[*] Testing connection: Listing all inventory items (GET /inventory_item)...")
    if env == "sandbox":
        print("    [NOTE] The Sandbox environment has a known bug where the bulk listing endpoint")
        print("           GET /inventory_item often returns a 500 error even when items exist.")
        print("           Direct lookup by SKU (GET /inventory_item/{sku}) works reliably.")
    url_items = f"{api_host}/sell/inventory/v1/inventory_item?limit=5"
    req_items = Request(url_items, headers=headers, method="GET")
    
    try:
        with urlopen(req_items) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            print("[+] Listing items successful!")
            print(f"Total inventory items found in {env_label}: {res_data.get('total', 0)}")
            for item in res_data.get("inventoryItems", []):
                print(f"  - SKU: {item.get('sku')}, Title: {item.get('product', {}).get('title')}")
    except HTTPError as e:
        print(f"[-] HTTP Error listing items (expected Sandbox bug): {e.code} {e.reason}")
    except Exception as e:
        print(f"[-] Error: {e}")

    # 6. Test Delete Inventory Item (DELETE /inventory_item/{sku})
    print("\n[*] Testing connection: Cleaning up/Deleting test item (DELETE /inventory_item/test-sku-001)...")
    req_delete = Request(url_put_item, headers=headers, method="DELETE")
    
    try:
        with urlopen(req_delete) as response:
            print(f"[+] Cleaned up test item successfully! Status Code: {response.status}")
    except HTTPError as e:
        print(f"[-] HTTP Error deleting item: {e.code} {e.reason}")
    except Exception as e:
        print(f"[-] Error deleting item: {e}")

    # 7. Test Get Locations
    print("\n[*] Testing connection: Fetching merchant locations (GET /location)...")
    url_locations = f"{api_host}/sell/inventory/v1/location?limit=5"
    req_locations = Request(url_locations, headers=headers, method="GET")
    
    try:
        with urlopen(req_locations) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            print("[+] Connection successful!")
            print(f"Total merchant locations found in {env_label}: {res_data.get('total', 0)}")
            print("Response preview:")
            print(json.dumps(res_data, indent=4))
    except HTTPError as e:
        print(f"[-] HTTP Error: {e.code} {e.reason}")
        try:
            err_body = e.read().decode("utf-8")
            print(f"Error details: {err_body}")
        except Exception:
            pass
    except Exception as e:
        print(f"[-] Error: {e}")

if __name__ == "__main__":
    test_api()

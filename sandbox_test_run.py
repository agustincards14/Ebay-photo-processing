import os
import json
import sys
import urllib.request
from urllib.error import HTTPError
from pathlib import Path

# Add project root to path to load authentication helpers
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.append(str(PROJECT_ROOT))
from ebay_oauth_helper import check_and_get_token, ENVIRONMENTS

def make_request(url, method="GET", payload=None, headers=None):
    """Utility function to make HTTP requests with proper error printing."""
    encoded_payload = None
    if payload is not None:
        encoded_payload = json.dumps(payload).encode("utf-8")
        
    req = urllib.request.Request(url, data=encoded_payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            status = response.status
            body_bytes = response.read()
            body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            return status, body
    except HTTPError as e:
        err_body_str = ""
        try:
            err_body_bytes = e.read()
            err_body_str = err_body_bytes.decode("utf-8")
        except Exception:
            pass
        return e.code, err_body_str
    except Exception as e:
        return 500, str(e)

def get_or_create_location(api_host, headers):
    print("[*] Checking Merchant Location 'STORE_MAIN'...")
    url = f"{api_host}/sell/inventory/v1/location/STORE_MAIN"
    status, res = make_request(url, method="GET", headers=headers)
    
    if status == 200:
        print("[+] Merchant Location 'STORE_MAIN' exists and is ready.")
        return True
        
    print("[*] 'STORE_MAIN' not found. Creating a new warehouse location...")
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
    status, res = make_request(url, method="POST", payload=location_payload, headers=headers)
    if status in (200, 201, 204):
        print("[+] Successfully created location 'STORE_MAIN'.")
        return True
    else:
        print(f"[-] Failed to create location: {status} - {res}")
        return False

def get_or_create_fulfillment_policy(api_host, headers):
    print("[*] Checking Fulfillment Policies...")
    url = f"{api_host}/sell/account/v1/fulfillment_policy?marketplace_id=EBAY_US"
    status, res = make_request(url, method="GET", headers=headers)
    
    if status == 200 and isinstance(res, dict):
        policies = res.get("fulfillmentPolicies", [])
        if policies:
            pol = policies[0]
            print(f"[+] Found existing fulfillment policy: '{pol.get('name')}' (ID: {pol.get('fulfillmentPolicyId')})")
            return pol.get("fulfillmentPolicyId")
            
    print("[*] No fulfillment policy found. Creating a default policy...")
    create_url = f"{api_host}/sell/account/v1/fulfillment_policy"
    policy_payload = {
        "name": "Sandbox Free Shipping Policy",
        "marketplaceId": "EBAY_US",
        "categoryTypes": [
            {
                "name": "ALL_EXCLUDING_MOTORS_VEHICLES",
                "default": True
            }
        ],
        "handlingTime": {
            "value": 3,
            "unit": "DAY"
        },
        "shippingOptions": [
            {
                "optionType": "DOMESTIC",
                "costType": "FLAT_RATE",
                "shippingServices": [
                    {
                        "shippingCarrierCode": "USPS",
                        "shippingServiceCode": "USPSFirstClass",
                        "freeShipping": True,
                        "sortOrder": 1
                    }
                ]
            }
        ],
        "localPickup": False,
        "freightShipping": False,
        "globalShipping": False,
        "description": "Fulfillment policy for photo products in sandbox envelope shipping."
    }
    status, res = make_request(create_url, method="POST", payload=policy_payload, headers=headers)
    if status in (200, 201) and isinstance(res, dict):
        policy_id = res.get("fulfillmentPolicyId")
        print(f"[+] Created shipping policy: ID {policy_id}")
        return policy_id
    else:
        print(f"[-] Failed to create shipping policy: {status} - {res}")
        return None

def get_or_create_return_policy(api_host, headers):
    print("[*] Checking Return Policies...")
    url = f"{api_host}/sell/account/v1/return_policy?marketplace_id=EBAY_US"
    status, res = make_request(url, method="GET", headers=headers)
    
    if status == 200 and isinstance(res, dict):
        policies = res.get("returnPolicies", [])
        if policies:
            pol = policies[0]
            print(f"[+] Found existing return policy: '{pol.get('name')}' (ID: {pol.get('returnPolicyId')})")
            return pol.get("returnPolicyId")
            
    print("[*] No return policy found. Creating a default return policy...")
    create_url = f"{api_host}/sell/account/v1/return_policy"
    policy_payload = {
        "name": "Sandbox Return Policy",
        "marketplaceId": "EBAY_US",
        "categoryTypes": [
            {
                "name": "ALL_EXCLUDING_MOTORS_VEHICLES",
                "default": True
            }
        ],
        "returnsAccepted": True,
        "refundMethod": "MONEY_BACK",
        "returnPeriod": {
            "unit": "DAY",
            "value": 30
        },
        "returnShippingCostPayer": "BUYER",
        "description": "Standard 30 day returns policy for sandbox"
    }
    status, res = make_request(create_url, method="POST", payload=policy_payload, headers=headers)
    if status in (200, 201) and isinstance(res, dict):
        policy_id = res.get("returnPolicyId")
        print(f"[+] Created return policy: ID {policy_id}")
        return policy_id
    else:
        print(f"[-] Failed to create return policy: {status} - {res}")
        return None

def get_or_create_payment_policy(api_host, headers):
    print("[*] Checking Payment Policies...")
    url = f"{api_host}/sell/account/v1/payment_policy?marketplace_id=EBAY_US"
    status, res = make_request(url, method="GET", headers=headers)
    
    if status == 200 and isinstance(res, dict):
        policies = res.get("paymentPolicies", [])
        if policies:
            pol = policies[0]
            print(f"[+] Found existing payment policy: '{pol.get('name')}' (ID: {pol.get('paymentPolicyId')})")
            return pol.get("paymentPolicyId")
            
    print("[*] No payment policy found. Creating a default payment policy...")
    create_url = f"{api_host}/sell/account/v1/payment_policy"
    policy_payload = {
        "name": "Sandbox Payment Policy",
        "marketplaceId": "EBAY_US",
        "categoryTypes": [
            {
                "name": "ALL_EXCLUDING_MOTORS_VEHICLES",
                "default": True
            }
        ],
        "description": "Standard payment policy for sandbox",
        "immediatePay": False
    }
    status, res = make_request(create_url, method="POST", payload=policy_payload, headers=headers)
    if status in (200, 201) and isinstance(res, dict):
        policy_id = res.get("paymentPolicyId")
        print(f"[+] Created payment policy: ID {policy_id}")
        return policy_id
    else:
        print(f"[-] Failed to create payment policy: {status} - {res}")
        return None

def run_test(env):
    api_host = ENVIRONMENTS[env]["api_host"]
    print("=" * 60)
    print(f"      Running Photo Listing Sandbox Test ({env.upper()})")
    print("=" * 60)
    
    access_token = check_and_get_token(env=env)
    if not access_token:
        print(f"[-] Authentication failed: Token could not be retrieved for {env.upper()}.")
        return
        
    headers = {
        "Content-Type": "application/json",
        "Content-Language": "en-US",
        "Authorization": f"Bearer {access_token}"
    }
    
    # 1. Setup location and policies
    if not get_or_create_location(api_host, headers):
        return
        
    fulfillment_id = get_or_create_fulfillment_policy(api_host, headers)
    return_id = get_or_create_return_policy(api_host, headers)
    payment_id = get_or_create_payment_policy(api_host, headers)
    
    if not (fulfillment_id and return_id and payment_id):
        print("[-] Error: Missing policy configurations. Aborting test run.")
        return
        
    # 2. Stage/PUT the Inventory Item
    item_json_path = PROJECT_ROOT / "test" / "sandbox_test_product" / "inventory_item.json"
    if not item_json_path.exists():
        print(f"[-] Error: Could not find {item_json_path}")
        return
        
    with open(item_json_path, "r") as f:
        inventory_item = json.load(f)
        
    sku = "sandbox-test-sku-photo-001"
    print(f"\n[*] Staging Inventory Item (PUT /inventory_item/{sku})...")
    item_url = f"{api_host}/sell/inventory/v1/inventory_item/{sku}"
    status, res = make_request(item_url, method="PUT", payload=inventory_item, headers=headers)
    
    if status == 204:
        print(f"[+] Success: Staged Inventory Item for SKU '{sku}'.")
    else:
        print(f"[-] Failed to stage Inventory Item: status {status} - details: {res}")
        return

    # 3. Stage/POST the Offer
    offer_json_path = PROJECT_ROOT / "test" / "sandbox_test_product" / "offer.json"
    with open(offer_json_path, "r") as f:
        offer_data = json.load(f)
        
    # Inject active policy IDs
    offer_data["sku"] = sku
    offer_data["listingPolicies"]["fulfillmentPolicyId"] = fulfillment_id
    offer_data["listingPolicies"]["returnPolicyId"] = return_id
    offer_data["listingPolicies"]["paymentPolicyId"] = payment_id
    
    print("\n[*] Staging Offer (POST /offer)...")
    offer_url = f"{api_host}/sell/inventory/v1/offer"
    status, res = make_request(offer_url, method="POST", payload=offer_data, headers=headers)
    
    if status == 201 and isinstance(res, dict):
        offer_id = res.get("offerId")
        print(f"[+] Success: Offer created with ID: {offer_id}")
    else:
        print(f"[-] Failed to stage Offer: status {status} - details: {res}")
        # Clean up staged item
        make_request(item_url, method="DELETE", headers=headers)
        return

    # 4. Publish the Offer
    print(f"\n[*] Publishing Offer '{offer_id}' (POST /offer/{offer_id}/publish)...")
    publish_url = f"{api_host}/sell/inventory/v1/offer/{offer_id}/publish"
    status, res = make_request(publish_url, method="POST", headers=headers)
    
    listing_id = None
    if status == 200 and isinstance(res, dict):
        listing_id = res.get("listingId")
        print(f"[+] SUCCESS! The item is live on eBay.")
        print(f"    Listing ID: {listing_id}")
        if env == "sandbox":
            print(f"    Sandbox Listing URL: https://sandbox.ebay.com/itm/{listing_id}")
        else:
            print(f"    Production Listing URL: https://www.ebay.com/itm/{listing_id}")
    else:
        print(f"[-] Failed to publish offer: status {status} - details: {res}")

    # 5. Interactive Teardown
    print("\n" + "=" * 50)
    print("                    TEARDOWN / CLEANUP")
    print("=" * 50)
    input("Press ENTER to withdraw the listing and clean up sandbox data...")
    
    if listing_id:
        print(f"[*] Withdrawing/ending listing '{listing_id}'...")
        withdraw_url = f"{api_host}/sell/inventory/v1/offer/{offer_id}/withdraw"
        w_status, w_res = make_request(withdraw_url, method="POST", headers=headers)
        if w_status == 200:
            print("[+] Successfully withdrew/ended listing.")
        else:
            print(f"[-] Failed to withdraw listing: {w_status} - {w_res}")
            
    print(f"[*] Deleting offer '{offer_id}'...")
    del_offer_url = f"{api_host}/sell/inventory/v1/offer/{offer_id}"
    o_status, o_res = make_request(del_offer_url, method="DELETE", headers=headers)
    if o_status == 204:
        print("[+] Successfully deleted offer.")
    else:
        print(f"[-] Failed to delete offer: {o_status} - {o_res}")
        
    print(f"[*] Deleting inventory item SKU '{sku}'...")
    del_item_url = f"{api_host}/sell/inventory/v1/inventory_item/{sku}"
    i_status, i_res = make_request(del_item_url, method="DELETE", headers=headers)
    if i_status == 204:
        print("[+] Successfully deleted inventory item.")
    else:
        print(f"[-] Failed to delete inventory item: {i_status} - {i_res}")
        
    print("\n[+] Cleanup complete! Test run finished successfully.")

if __name__ == "__main__":
    env_choice = "sandbox"
    if len(sys.argv) > 1:
        env_choice = sys.argv[1].lower()
    if env_choice not in ("sandbox", "production"):
        env_choice = "sandbox"
    run_test(env_choice)

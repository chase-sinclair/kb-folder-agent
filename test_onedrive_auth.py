import os
import json
import msal
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
TENANT_ID = os.environ.get("AZURE_TENANT_ID", "consumers")
SCOPES = ["Files.Read", "Files.Read.All", "User.Read"]
TOKEN_CACHE_PATH = ".onedrive_token_cache.json"
ONEDRIVE_FOLDER = os.environ.get("ONEDRIVE_FOLDER", "test-kb")

def load_cache():
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_PATH):
        with open(TOKEN_CACHE_PATH, "r") as f:
            cache.deserialize(f.read())
    return cache

def save_cache(cache):
    if cache.has_state_changed:
        with open(TOKEN_CACHE_PATH, "w") as f:
            f.write(cache.serialize())

def get_token():
    cache = load_cache()
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache
    )

    # Try silent auth first
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            save_cache(cache)
            return result["access_token"]

    # Fall back to device code flow
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise ValueError("Failed to create device flow")

    print("\n" + "="*50)
    print("ACTION REQUIRED:")
    print(f"1. Go to: {flow['verification_uri']}")
    print(f"2. Enter code: {flow['user_code']}")
    print("="*50 + "\n")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise ValueError(f"Auth failed: {result.get('error_description')}")

    save_cache(cache)
    return result["access_token"]

def test_onedrive():
    print("Authenticating with Microsoft...")
    token = get_token()
    print("Auth successful!")

    headers = {"Authorization": f"Bearer {token}"}

    # Test: list files in ONEDRIVE_FOLDER
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{ONEDRIVE_FOLDER}:/children"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        items = response.json().get("value", [])
        print(f"\nFound {len(items)} items in OneDrive/{ONEDRIVE_FOLDER}:")
        for item in items:
            print(f"  - {item['name']} ({'folder' if 'folder' in item else 'file'})")
    else:
        print(f"Graph API error: {response.status_code} {response.text}")

if __name__ == "__main__":
    test_onedrive()

#!/bin/zsh

# Ensure we are in the Ebay directory containing the helper script and virtual environment
if [[ ! -f "ebay_oauth_helper.py" ]] || [[ ! -d ".venv" ]]; then
    echo "[-] Error: This script must be run (sourced) from the eBay project directory."
    return 1 2>/dev/null || exit 1
fi

# Activate virtual environment
source .venv/bin/activate

TARGET_STORE="photo_vault"
ACTIVE_ENV="${1:-${EBAY_ENV:-production}}"
ACTIVE_ENV=$(echo "$ACTIVE_ENV" | tr '[:upper:]' '[:lower:]')

echo "[*] Fetching eBay API tokens for Store: PHOTO_VAULT ($ACTIVE_ENV)..."

# 1. Fetch Sandbox Token
SBX_TOKEN=$(python ebay_oauth_helper.py --token --store "$TARGET_STORE" --env sandbox 2>/dev/null)
if [[ $? -eq 0 ]] && [[ -n "$SBX_TOKEN" ]]; then
    export EBAY_SANDBOX_ACCESS_TOKEN="$SBX_TOKEN"
    LAST_FIVE_SBX="${SBX_TOKEN: -5}"
    echo "[+] Sandbox Token loaded! ...$LAST_FIVE_SBX"
else
    echo "[-] Warning: Sandbox token not configured for photo_vault."
fi

# 2. Fetch Production Token
PRD_TOKEN=$(python ebay_oauth_helper.py --token --store "$TARGET_STORE" --env production 2>/dev/null)
if [[ $? -eq 0 ]] && [[ -n "$PRD_TOKEN" ]]; then
    export EBAY_PRODUCTION_ACCESS_TOKEN="$PRD_TOKEN"
    LAST_FIVE_PRD="${PRD_TOKEN: -5}"
    echo "[+] Production Token loaded! ...$LAST_FIVE_PRD"
else
    echo "[-] Warning: Production token not configured for photo_vault."
fi

# 3. Set the active token, store, and environment
export EBAY_STORE="$TARGET_STORE"

if [[ "$ACTIVE_ENV" == "production" ]]; then
    if [[ -n "$EBAY_PRODUCTION_ACCESS_TOKEN" ]]; then
        export EBAY_ACCESS_TOKEN="$EBAY_PRODUCTION_ACCESS_TOKEN"
        export EBAY_ENV="production"
        echo "[+] Active Store: photo_vault | Active Environment: PRODUCTION"
    else
        export EBAY_ACCESS_TOKEN="$EBAY_SANDBOX_ACCESS_TOKEN"
        export EBAY_ENV="sandbox"
        echo "[-] Warning: Production token is empty. Falling back active environment to SANDBOX."
    fi
else
    export EBAY_ACCESS_TOKEN="$EBAY_SANDBOX_ACCESS_TOKEN"
    export EBAY_ENV="sandbox"
    echo "[+] Active Store: photo_vault | Active Environment: SANDBOX"
fi

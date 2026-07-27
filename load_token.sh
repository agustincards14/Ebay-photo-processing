#!/bin/zsh

# Ensure we are in the Ebay directory containing the helper script and virtual environment
if [[ ! -f "ebay_oauth_helper.py" ]] || [[ ! -d ".venv" ]]; then
    echo "[-] Error: This script must be run (sourced) from the eBay project directory."
    return 1 2>/dev/null || exit 1
fi

# Activate virtual environment
source .venv/bin/activate

# Parse arguments or prompt interactively for store selection
INPUT_ARG1="$1"
INPUT_ARG2="$2"

TARGET_STORE=""
ACTIVE_ENV=""

if [[ -n "$INPUT_ARG1" ]]; then
    ARG1_LOWER=$(echo "$INPUT_ARG1" | tr '[:upper:]' '[:lower:]')
    if [[ "$ARG1_LOWER" == "personal" ]] || [[ "$ARG1_LOWER" == "photo_vault" ]] || [[ "$ARG1_LOWER" == "photovault" ]]; then
        TARGET_STORE="$ARG1_LOWER"
        ACTIVE_ENV="${INPUT_ARG2:-${EBAY_ENV:-production}}"
    else
        ACTIVE_ENV="$ARG1_LOWER"
    fi
fi

if [[ -z "$TARGET_STORE" ]]; then
    echo "Select eBay Store Account:"
    echo "1) Photo Vault (Default)"
    echo "2) Personal"
    read "STORE_CHOICE?Enter choice [1]: "
    
    STORE_CHOICE=$(echo "$STORE_CHOICE" | tr '[:upper:]' '[:lower:]' | xargs)
    if [[ "$STORE_CHOICE" == "2" ]] || [[ "$STORE_CHOICE" == "personal" ]]; then
        TARGET_STORE="personal"
    else
        TARGET_STORE="photo_vault"
    fi
fi

ACTIVE_ENV="${ACTIVE_ENV:-${EBAY_ENV:-production}}"
ACTIVE_ENV=$(echo "$ACTIVE_ENV" | tr '[:upper:]' '[:lower:]')

echo "\n[*] Fetching eBay API tokens for Store: $(echo "$TARGET_STORE" | tr '[:lower:]' '[:upper:]')..."

# 1. Fetch Sandbox Token
SBX_TOKEN=$(python ebay_oauth_helper.py --token --store "$TARGET_STORE" --env sandbox 2>/dev/null)
if [[ $? -eq 0 ]] && [[ -n "$SBX_TOKEN" ]]; then
    export EBAY_SANDBOX_ACCESS_TOKEN="$SBX_TOKEN"
    LAST_FIVE_SBX="${SBX_TOKEN: -5}"
    echo "[+] Sandbox Token loaded! ...$LAST_FIVE_SBX"
else
    echo "[-] Warning: Sandbox token not configured. Run 'python ebay_oauth_helper.py --store $TARGET_STORE --env sandbox' to authenticate."
fi

# 2. Fetch Production Token
PRD_TOKEN=$(python ebay_oauth_helper.py --token --store "$TARGET_STORE" --env production 2>/dev/null)
if [[ $? -eq 0 ]] && [[ -n "$PRD_TOKEN" ]]; then
    export EBAY_PRODUCTION_ACCESS_TOKEN="$PRD_TOKEN"
    LAST_FIVE_PRD="${PRD_TOKEN: -5}"
    echo "[+] Production Token loaded! ...$LAST_FIVE_PRD"
else
    echo "[-] Warning: Production token not configured. Run 'python ebay_oauth_helper.py --store $TARGET_STORE --env production' to authenticate."
fi

# 3. Set the active token, store, and environment
export EBAY_STORE="$TARGET_STORE"

if [[ "$ACTIVE_ENV" == "production" ]]; then
    if [[ -n "$EBAY_PRODUCTION_ACCESS_TOKEN" ]]; then
        export EBAY_ACCESS_TOKEN="$EBAY_PRODUCTION_ACCESS_TOKEN"
        export EBAY_ENV="production"
        echo "[+] Active Store: $TARGET_STORE | Active Environment: PRODUCTION"
    else
        export EBAY_ACCESS_TOKEN="$EBAY_SANDBOX_ACCESS_TOKEN"
        export EBAY_ENV="sandbox"
        echo "[-] Warning: Production token is empty. Falling back active environment to SANDBOX."
    fi
else
    export EBAY_ACCESS_TOKEN="$EBAY_SANDBOX_ACCESS_TOKEN"
    export EBAY_ENV="sandbox"
    echo "[+] Active Store: $TARGET_STORE | Active Environment: SANDBOX"
fi

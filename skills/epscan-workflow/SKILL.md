---
name: epscan-workflow
description: "Guides agents on processing scanned vintage photos, generating structured metadata, and creating eBay Inventory API compatible inventory_item.json files."
---

# eBay Photo Processing & Inventory Listing Workflow

This skill teaches agents how to sequentially process raw scanned vintage photographs (front and back pairs), generate AI-enriched metadata using Gemini 2.5 Pro, and convert the metadata into structured JSON payloads compatible with the eBay Sell Inventory API.

---

## 📋 Sequential Workflow Steps

### Step 1: Scan Directory Discovery
* Check the target directory (e.g. `/Users/agustinbjr/Ebay/test` or another user-specified path) for raw `.jpg` / `.jpeg` files.
* Ensure there is an even number of files. Sort them alphabetically to pair sequential files `(n, n+1)` as the front and back of a photograph.

### Step 2: Extract Metadata via Gemini 2.5 Pro
* Activate the Python virtual environment:
  ```bash
  source .venv/bin/activate
  ```
* Run `process_photos.py <directory>` to pair front/back images, move them into individual subfolders (named after the front image), and request structured metadata from Gemini.
* Verify that a `metadata.json` is generated inside each subfolder, matching the `PhotoMetadata` schema:
  * `title`, `year`, `keywords`, `location`, `description`, `size`, `price`.

### Step 3: Convert Metadata to eBay Inventory Schema
* Run `convert_metadata.py <directory>` to convert the raw `metadata.json` files to `inventory_item.json` (the payload schema for the eBay `PUT /inventory_item/{sku}` endpoint).
* The conversion logic must map:
  * `availability`: Quantity defaults to `1`.
  * `condition`: Set to `"USED_VERY_GOOD"`.
  * `conditionDescription`: Standardized vintage photo description containing the year and dimensions.
  * `product`:
    * `title`: Sourced from metadata (truncated to 80 characters limit).
    * `description`: The detailed setting description.
    * `imageUrls`: HTTPS URLs for front and back images (e.g. `https://example.com/scans/{subfolder}/{filename}`).
    * `aspects`: Mapping core item specifics:
      * `Subject`: Keywords list
      * `Year of Production`: `[year]`
      * `Size`: `[size]`
      * `Vintage`: `["Yes"]`
      * `Image Orientation`: `["Portrait"]` or `["Landscape"]` (calculated from size dimensions)
      * `Type`: `["Photograph"]`
      * `Location`: `[location]`
      * `Original/Reprint`: `["Original Print"]`
      * `Style`: `["Photojournalism"]`
      * `Features`: `["One of a Kind (OOAK)"]`
      * `Production Technique`: `["Photocopy"]`
      * `Size Type/Largest Dimension`: `["Small (Up to 7\")"]`
      * `Listed By`: `["Dealer or Reseller"]`
      * `Year`: `[year]`

### Step 4: Validate and Test Connection
* Make sure `inventory_item.json` is created in each subdirectory and conforms to the schema in the OpenAPI specification `sell_inventory_v1_oas3.json`.
* Run test calls using `test_ebay_api.py` to confirm everything works properly.

---

## 🛠️ Tools & Scripts

This skill utilizes the following scripts in the project root:
* [process_photos.py](file:///Users/agustinbjr/Ebay/process_photos.py): Manages folder grouping and Gemini API metadata generation.
* [convert_metadata.py](file:///Users/agustinbjr/Ebay/convert_metadata.py): Transforms metadata to eBay format.
* [test_ebay_api.py](file:///Users/agustinbjr/Ebay/test_ebay_api.py): Tests the generated details against eBay APIs.

# EPSCAN Project

## Overview
This project processes scanned vintage photographs (front and back pairs) using AI. It pairs sequential `.jpg` images, organizes them into subfolders, and leverages the Google GenAI SDK (Gemini 3.5 Flash) with structured JSON outputs to generate robust metadata (including title, year, location, keywords, and text from the back of the photo).

## Libraries & Frameworks
- **Python**: Core language.
- **Google GenAI SDK (`google-genai`)**: Used for multimodal image analysis.
- **Pydantic**: Used to define strict JSON schemas (e.g., `PhotoMetadata`).
- **Pillow (`PIL`)**: Used for reading image dimensions to calculate physical sizes (assuming 300 DPI).

## Environment Setup
- The generative AI client requires the `GEMINI_API_KEY` environment variable to be set.
- Always activate the virtual environment before executing scripts:
  ```bash
  source .venv/bin/activate
  ```

## Workflows

### 1. Photo Processing and Metadata Extraction
- **Goal**: Read raw scanned JPEGs, pair them sequentially (front/back), move them to subfolders, and run Gemini to extract metadata.
- **Trigger**: 
  1. Organize photos into folders: `python organize_photos.py [/path/to/directory]`
  2. Generate metadata for a folder: `python generate_metadata.py /path/to/item-folder`
- **Outputs**: Generates subfolders for each photo pair, containing the front and back JPEGs, and a `metadata.json` containing the Gemini-generated metadata inside each folder.

### 2. eBay Inventory API Listing Workflow
- **Goal**: Upload photo data directly to the eBay website using the eBay Sell Inventory API.
- **Detailed Structure**: Refer to [EBAY_INVENTORY_API.md](EBAY_INVENTORY_API.md) for endpoint details and JSON payload configurations.
  - Also refer to https://developer.ebay.com/api-docs/sell/inventory/overview.html and https://developer.ebay.com/api-docs/commerce/media/overview.html for more information on the eBay Sell Inventory API and the eBay Media API when needed
- **Mapping Logic**:
  - Condition: Maps to string enums like `USED_VERY_GOOD`.
  - Aspects: Maps metadata variables to key-value string arrays in `product.aspects`.

## Code Conventions
- **Structured Outputs**: Model interactions should continue to use `response_mime_type="application/json"` and `response_schema` mapped to Pydantic BaseModels to guarantee valid JSON return structures.
- **File Organization**: The script groups sequential images `(n, n+1)` as front and back respectively, generating an individual subfolder and a `metadata.json` file for each pair. 
- **Type Hinting**: Use Python type hints (Standard `list`, `str`, etc.) combined with Pydantic `Field` descriptions to guide the AI on expected metadata bounds.

## Prompts & AI Directives
- **Prompt Consistency**: Multimodal prompts must instruct the model to analyze both sides of the photo and use any handwriting, stamps, or marks on the back to infer dates, locations, and context.
- **Field Constraints**:
  - `title`: Keep descriptive but under 80 characters for eBay compatibility.
  - `price`: Estimated numeric USD value (e.g., `$10`).

## Agent Customization & Skills
If you are developing custom autonomous agents (using the Google Antigravity SDK) for this project, you can package these workflows into reusable **Skills**:
1. Create a `skills/epscan_workflow/` folder in your project.
2. Create a `SKILL.md` inside it with YAML frontmatter:
   ```yaml
   ---
   name: epscan-workflow
   description: "Guides agents on processing scanned photos and generating eBay bulk listing templates."
   ---
   # Skill Instructions
   ...
   ```
3. Load the skill path into your agent configuration:
   ```python
    config = LocalAgentConfig(
        skills_paths=["./skills"]
    )
   ```

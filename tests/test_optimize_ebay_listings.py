"""Unit tests for sync_ebay_live_status.py and optimize_ebay_listings.py."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optimize_ebay_listings import (
    ALLOWED_PUT_OFFER_KEYS,
    build_html_description,
    build_optimized_inventory_payload,
    build_optimized_offer_payload,
    fix_title_photo_keyword,
    load_taxonomy_constraints,
    map_time_period_manufactured,
)
from sync_ebay_live_status import classify_live_item


def test_classify_live_item_states():
    active_inv = {"statusCode": 200, "availability": {"shipToLocationAvailability": {"quantity": 1}}}
    active_offer = {
        "status": "PUBLISHED",
        "availableQuantity": 1,
        "listing": {"listingStatus": "ACTIVE", "soldQuantity": 0},
    }
    assert classify_live_item(active_inv, 200, active_offer) == "ACTIVE"

    sold_ended_offer = {
        "status": "PUBLISHED",
        "availableQuantity": 0,
        "listing": {"listingStatus": "ENDED", "soldQuantity": 1},
    }
    assert classify_live_item(active_inv, 200, sold_ended_offer) == "SOLD"

    sold_oos_inv = {"statusCode": 200, "availability": {"shipToLocationAvailability": {"quantity": 0}}}
    sold_oos_offer = {
        "status": "PUBLISHED",
        "availableQuantity": 0,
        "listing": {"listingStatus": "OUT_OF_STOCK", "soldQuantity": 1},
    }
    assert classify_live_item(sold_oos_inv, 200, sold_oos_offer) == "SOLD"

    ended_unsold_offer = {
        "status": "PUBLISHED",
        "availableQuantity": 1,
        "listing": {"listingStatus": "ENDED", "soldQuantity": 0},
    }
    assert classify_live_item(active_inv, 200, ended_unsold_offer) == "ENDED_UNSOLD"

    oos_unsold_offer = {
        "status": "PUBLISHED",
        "availableQuantity": 0,
        "listing": {"listingStatus": "OUT_OF_STOCK", "soldQuantity": 0},
    }
    assert classify_live_item(sold_oos_inv, 200, oos_unsold_offer) == "OUT_OF_STOCK_UNSOLD"

    unpub_offer = {"status": "UNPUBLISHED", "listing": {}}
    assert classify_live_item(active_inv, 200, unpub_offer) == "UNPUBLISHED"

    assert classify_live_item(active_inv, 404, {}) == "NOT_FOUND"


def test_map_time_period_manufactured():
    assert map_time_period_manufactured(1845) == "Pre-1850"
    assert map_time_period_manufactured(1890) == "1850-1899"
    assert map_time_period_manufactured(1920) == "1900-1924"
    assert map_time_period_manufactured(1945) == "1925-1949"
    assert map_time_period_manufactured(1958) == "1950-1959"
    assert map_time_period_manufactured(1965) == "1960-1969"
    assert map_time_period_manufactured(1975) == "1970-1979"
    assert map_time_period_manufactured(None) is None


def test_build_optimized_inventory_payload_aspects_and_selection_only(tmp_path):
    tax_data = {
        "aspects": [
            {
                "localizedAspectName": "Image Orientation",
                "aspectConstraint": {"aspectMode": "SELECTION_ONLY", "itemToAspectCardinality": "SINGLE"},
                "aspectValues": [{"localizedValue": "Landscape"}, {"localizedValue": "Portrait"}],
            },
            {
                "localizedAspectName": "Antique",
                "aspectConstraint": {"aspectMode": "SELECTION_ONLY", "itemToAspectCardinality": "SINGLE"},
                "aspectValues": [{"localizedValue": "Yes"}, {"localizedValue": "No"}],
            },
            {
                "localizedAspectName": "Country of Origin",
                "aspectConstraint": {"aspectMode": "SELECTION_ONLY", "itemToAspectCardinality": "SINGLE"},
                "aspectValues": [{"localizedValue": "United States"}, {"localizedValue": "Japan"}],
            },
        ]
    }
    tax_path = tmp_path / "tax.json"
    tax_path.write_text(json.dumps(tax_data))
    taxonomy = load_taxonomy_constraints(tax_path)

    live_inv = {
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "condition": "USED_EXCELLENT",
        "product": {
            "title": "Family on Porch, 1920s B&W Snapshot, USA",
            "description": "Old desc",
            "aspects": {
                "Year": ["circa 1920"],
                "Year of Production": ["circa 1920"],
                "Image Orientation": ["Square"],
                "Antique": ["No"],
                "Country of Origin": ["United States"],
            },
            "imageUrls": ["https://i.ebayimg.com/1.jpg", "https://i.ebayimg.com/2.jpg"],
        },
    }
    meta = {
        "title": "Family on Porch, 1920s B&W Snapshot, USA",
        "year": "circa 1920",
        "photo_type": "Snapshot",
        "description": "Three children on a wooden porch <circa 1920>.",
        "back_text_transcription": "Riggins Family & Co",
        "size": "3.5x3.5in",
        "location": "United States",
    }

    out = build_optimized_inventory_payload(live_inv, meta, taxonomy)
    aspects = out["product"]["aspects"]
    assert "Year" not in aspects
    assert aspects["Year of Production"] == ["1920"]
    assert aspects["Time Period Manufactured"] == ["1900-1924"]
    assert aspects["Format"] == ["Snapshot"]
    assert aspects["Antique"] == ["Yes"]
    assert aspects["Image Orientation"] == ["Portrait"]
    assert "Photo" in out["product"]["title"]
    assert len(out["product"]["title"]) <= 80
    assert out["availability"]["shipToLocationAvailability"]["quantity"] == 1


def test_build_optimized_offer_payload_strips_readonly_and_removes_best_offer():
    live_offer = {
        "offerId": "222185814011",
        "sku": "AIPH1-A1",
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "status": "PUBLISHED",
        "listing": {"listingId": "800447218756", "listingStatus": "ACTIVE", "soldQuantity": 0},
        "availableQuantity": 1,
        "categoryId": "262421",
        "merchantLocationKey": "HOME_BASE",
        "pricingSummary": {"price": {"value": "8.00", "currency": "USD"}},
        "listingPolicies": {
            "fulfillmentPolicyId": "111",
            "returnPolicyId": "222",
            "paymentPolicyId": "333",
            "bestOfferTerms": {"bestOfferEnabled": True, "autoAcceptPrice": {"value": "7.20", "currency": "USD"}},
        },
    }
    # Default target_price=None preserves live price ("19.95") while removing Best Offer & updating desc
    payload_keep, needs_keep = build_optimized_offer_payload(
        {**live_offer, "pricingSummary": {"price": {"value": "19.95", "currency": "USD"}}},
        target_price=None,
        listing_description="<div>New</div>",
    )
    assert needs_keep is True
    assert payload_keep["pricingSummary"]["price"] == {"value": "19.95", "currency": "USD"}
    assert "bestOfferTerms" not in payload_keep["listingPolicies"]
    assert payload_keep["listingDescription"] == "<div>New</div>"

    # Explicit target_price="24.99" updates price
    payload, needs_update = build_optimized_offer_payload(live_offer, "24.99")
    assert needs_update is True
    assert set(payload.keys()).issubset(ALLOWED_PUT_OFFER_KEYS)
    for forbidden in ("offerId", "sku", "marketplaceId", "format", "status", "listing"):
        assert forbidden not in payload
    assert payload["pricingSummary"]["price"] == {"value": "24.99", "currency": "USD"}
    assert "bestOfferTerms" not in payload["listingPolicies"]

    # Already no bestOfferTerms, matching listingDescription, target_price=None -> no-op
    already_done = {
        "availableQuantity": 1,
        "categoryId": "262421",
        "merchantLocationKey": "HOME_BASE",
        "listingDescription": "<div>Same</div>",
        "pricingSummary": {"price": {"value": "19.95", "currency": "USD"}},
        "listingPolicies": {"fulfillmentPolicyId": "111", "returnPolicyId": "222", "paymentPolicyId": "333"},
    }
    _, needs_update_2 = build_optimized_offer_payload(
        already_done, target_price=None, listing_description="<div>Same</div>"
    )
    assert needs_update_2 is False


def test_html_description_escaping_and_back_text():
    meta = {
        "title": "Couple & Dog <1955>",
        "description": "Smiling couple outdoors & a dog.",
        "back_text_transcription": "Mom & Dad '55",
        "size": "3.5x5.0in",
        "location": "Ohio",
    }
    aspects = {"Year of Production": ["1955"], "Time Period Manufactured": ["1950-1959"], "Format": ["Snapshot"]}
    html_out = build_html_description(meta, aspects)
    assert "<script" not in html_out.lower()
    assert "&lt;1955&gt;" in html_out
    assert "Back of Photo Inscription" in html_out
    assert "Mom &amp; Dad" in html_out

    meta_no_back = dict(meta, back_text_transcription="")
    html_no_back = build_html_description(meta_no_back, aspects)
    assert "Back of Photo Inscription" not in html_no_back


def test_fix_title_photo_keyword_length():
    t1 = "Women in 1950s Living Room, Vintage B&W Snapshot 1958"
    assert fix_title_photo_keyword(t1) == f"{t1} Photo"
    long_t = "Very Long Descriptive Vintage Black & White Snapshot Subject With Many Words 1958"
    fixed = fix_title_photo_keyword(long_t)
    assert "photo" in fixed.lower()
    assert len(fixed) <= 80


def test_run_ebay_workflow_build_inventory_item_payload(tmp_path):
    from run_ebay_workflow import build_inventory_item_payload

    meta = {
        "title": "Graduates on Lawn, 1924 Sepia Snapshot",
        "year": "circa 1924",
        "subjects": ["Graduate", "Campus"],
        "themes": ["Universities"],
        "location": "United States",
        "image_color": "Sepia",
        "photo_type": "Snapshot",
        "back_text_transcription": "Class of 24",
        "description": "Graduates standing on lawn.",
        "size": "3.5x3.5in",
    }
    payload = build_inventory_item_payload(tmp_path, meta, ["https://i.ebayimg.com/1.jpg"])
    aspects = payload["product"]["aspects"]
    assert "Year" not in aspects
    assert aspects["Year of Production"] == ["1924"]
    assert aspects["Time Period Manufactured"] == ["1900-1924"]
    assert aspects["Format"] == ["Snapshot"]
    assert aspects["Antique"] == ["Yes"]
    assert aspects["Image Orientation"] == ["Portrait"]
    assert payload["product"]["description"].startswith("<div")
    assert "Photo" in payload["product"]["title"]
    assert len(payload["product"]["title"]) <= 80


if __name__ == "__main__":
    import tempfile
    import unittest

    class OptimizerTestSuite(unittest.TestCase):
        def test_classify_live_item_states(self):
            test_classify_live_item_states()

        def test_map_time_period_manufactured(self):
            test_map_time_period_manufactured()

        def test_build_optimized_inventory_payload(self):
            with tempfile.TemporaryDirectory() as d:
                test_build_optimized_inventory_payload_aspects_and_selection_only(Path(d))

        def test_build_optimized_offer_payload(self):
            test_build_optimized_offer_payload_strips_readonly_and_removes_best_offer()

        def test_html_description_escaping_and_back_text(self):
            test_html_description_escaping_and_back_text()

        def test_fix_title_photo_keyword_length(self):
            test_fix_title_photo_keyword_length()

        def test_run_ebay_workflow_build_inventory_item_payload(self):
            with tempfile.TemporaryDirectory() as d:
                test_run_ebay_workflow_build_inventory_item_payload(Path(d))

    unittest.main()

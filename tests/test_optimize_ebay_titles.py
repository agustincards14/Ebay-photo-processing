"""Unit tests for Phase 2 AI Title Optimizer (optimize_ebay_titles.py)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optimize_ebay_titles import (
    compute_title_cache_key,
    get_cached_title,
    save_cached_title,
    select_diverse_sample,
    validate_seo_title,
)


class TitleOptimizerTests(unittest.TestCase):
    def test_validate_seo_title_accepts_clean_title(self):
        meta_1920 = {"year": "1920"}
        t1 = "Nina McCully, Family Garden Foliage - Antique 1920s Sepia Snapshot Photo"
        self.assertLessEqual(len(t1), 80)
        self.assertEqual(validate_seo_title(t1, meta_1920), [])

        meta_1944 = {"year": "1944"}
        t2 = "Toddler First Steps, Tulsa Oklahoma Yard - Vintage 1940s B&W Snapshot Photo"
        self.assertLessEqual(len(t2), 80)
        self.assertEqual(validate_seo_title(t2, meta_1944), [])

    def test_validate_seo_title_catches_all_violations(self):
        meta_1958 = {"year": "1958"}
        # Too short & missing required words / separator
        errs = validate_seo_title("Short title", meta_1958)
        self.assertTrue(any("Too short" in e for e in errs))
        self.assertTrue(any(" - " in e for e in errs))
        self.assertTrue(any("Photo" in e for e in errs))
        self.assertTrue(any("Vintage" in e for e in errs))
        self.assertTrue(any("1950s" in e for e in errs))

        # Too long (> 80)
        long_t = "Subject Details - Vintage 1950s B&W Snapshot Photo " + ("Word " * 10)
        self.assertTrue(any("Too long" in e for e in validate_seo_title(long_t, meta_1958)))

        # Black & White, spam, duplicate, too many commas
        bad_t = "RARE Couple, A, B, Couple - Vintage 1950s Black & White Snapshot Photo"
        bad_errs = validate_seo_title(bad_t, meta_1958)
        self.assertTrue(any("commas" in e.lower() for e in bad_errs))
        self.assertTrue(any("B&W" in e for e in bad_errs))
        self.assertTrue(any("spam" in e.lower() for e in bad_errs))
        self.assertTrue(any("duplicate" in e.lower() for e in bad_errs))

        # Pre-1926 requires Antique instead of Vintage
        meta_1924 = {"year": "1924"}
        t_no_antq = "College Graduates, Campus Lawn - Vintage 1920s Sepia Snapshot Photo"
        antq_errs = validate_seo_title(t_no_antq, meta_1924)
        self.assertTrue(any("Antique" in e for e in antq_errs))

    def test_cache_roundtrip_and_invalidation(self):
        with tempfile.TemporaryDirectory() as td:
            cdir = Path(td)
            src1 = {"sku": "SKU-1", "current_title": "Old", "year": "1958"}
            k1 = compute_title_cache_key(src1, "gemini-3.8-flash")
            self.assertIsNone(get_cached_title(cdir, "SKU-1", k1))
            save_cached_title(cdir, "SKU-1", k1, "Vintage 1950s Test Original Snapshot Photo", "gemini-3.8-flash")
            self.assertEqual(
                get_cached_title(cdir, "SKU-1", k1),
                "Vintage 1950s Test Original Snapshot Photo",
            )
            # Metadata change invalidates key
            src2 = {"sku": "SKU-1", "current_title": "Old", "year": "1962"}
            k2 = compute_title_cache_key(src2, "gemini-3.8-flash")
            self.assertNotEqual(k1, k2)
            self.assertIsNone(get_cached_title(cdir, "SKU-1", k2))

    def test_select_diverse_sample_spans_folders_and_excludes_sold(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            records = []
            for d in ("EPSCAN_7_26", "EPSCAN_7_30", "EPSCAN_8_23", "EPSCAN_9_20"):
                for i in range(8):
                    sku = f"{d}-{i}"
                    fdir = root / d / sku
                    fdir.mkdir(parents=True)
                    (fdir / "metadata.json").write_text(
                        json.dumps({"year": "1920" if i == 0 else "1955", "title": "Short"}),
                        encoding="utf-8",
                    )
                    records.append(
                        {
                            "epscan_dir": d,
                            "sku": sku,
                            "folder_path": str(fdir),
                            "classification": "SOLD" if i == 7 else "ACTIVE",
                            "live_title": "Short",
                        }
                    )
            sample = select_diverse_sample(records, root, n=25)
            self.assertEqual(len(sample), 25)
            self.assertTrue(all(r["classification"] == "ACTIVE" for r in sample))
            dirs_covered = {r["epscan_dir"] for r in sample}
            self.assertEqual(dirs_covered, {"EPSCAN_7_26", "EPSCAN_7_30", "EPSCAN_8_23", "EPSCAN_9_20"})


if __name__ == "__main__":
    unittest.main()

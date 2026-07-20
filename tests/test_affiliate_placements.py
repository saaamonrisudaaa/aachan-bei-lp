import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "assets" / "affiliate-banners.json"
SCRIPT_PATH = ROOT / "assets" / "affiliate-banner.js"


class AffiliatePlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cls.script = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_config_defines_distinct_sidebar_and_footer_catalogs(self):
        self.assertEqual(self.config["version"], 9)
        sidebar = set(self.config["placements"]["sidebar"]["adIds"])
        footer = set(self.config["placements"]["footer"]["adIds"])

        self.assertNotIn("rakuten-travel-main", sidebar)
        self.assertIn("rakuten-travel-main", footer)
        self.assertIn("rakuten-ramen", sidebar)
        self.assertIn("rakuten-ramen", footer)
        self.assertIn("rakuten-shop-feature", footer)
        self.assertNotIn("rakuten-shop-feature", sidebar)

    def test_active_ads_have_clear_disclosure_labels(self):
        active_ads = [ad for ad in self.config["ads"] if ad.get("active", True)]
        self.assertTrue(active_ads)
        for ad in active_ads:
            self.assertEqual(ad["label"], "広告・PR", ad["id"])

    def test_generic_article_has_footer_fallback_candidates(self):
        ads = {ad["id"]: ad for ad in self.config["ads"]}
        travel_paths = ads["rakuten-travel-main"]["targeting"]["paths"]
        shop_paths = ads["rakuten-shop-feature"]["targeting"]["paths"]
        self.assertIn("/restaurant-*.html", travel_paths)
        self.assertIn("/restaurant-*.html", shop_paths)

    def test_script_uses_non_sticky_placement_and_avoids_duplicates(self):
        self.assertNotIn("position:fixed", self.script.replace(" ", ""))
        self.assertIn(".seo-facts.achanbay-affiliate-host", self.script)
        self.assertIn("const candidates = eligible.filter(ad => !usedIds.has(ad.id));", self.script)
        self.assertIn('ad_placement: placement', self.script)
        self.assertIn('"IntersectionObserver" in window', self.script)
        self.assertIn('window.matchMedia("(max-width: 860px)")', self.script)
        self.assertIn('? ["footer"]', self.script)
        self.assertIn('@media(max-width:860px){.achanbay-affiliate-card--sidebar{display:none}}', self.script)

    def test_loader_is_limited_to_existing_monetized_pages(self):
        html_files = sorted(ROOT.glob("*.html"))
        banner_files = []
        for path in html_files:
            text = path.read_text(encoding="utf-8")
            if "affiliate-banner.js" in text:
                banner_files.append(path)
                self.assertIn("affiliate-banner.js?v=20260720", text, path.name)
                self.assertIn('class="site-footer"', text, path.name)

        self.assertEqual(len(banner_files), 12)
        restaurant_banner_files = [
            path for path in banner_files if re.fullmatch(r"restaurant-\d{3}\.html", path.name)
        ]
        self.assertEqual(len(restaurant_banner_files), 5)
        for path in restaurant_banner_files:
            text = path.read_text(encoding="utf-8")
            self.assertIn('content="index,follow,max-image-preview:large"', text, path.name)


if __name__ == "__main__":
    unittest.main()

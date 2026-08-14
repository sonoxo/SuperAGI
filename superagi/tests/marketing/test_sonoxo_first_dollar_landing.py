import unittest

from superagi.marketing.sonoxo_first_dollar_landing import FirstDollarLandingPage
from superagi.marketing.sonoxo_sales_fabric import MERCH_DESTINATION, SOUNDCLOUD_DESTINATION


class TestFirstDollarLandingPage(unittest.TestCase):
    def setUp(self):
        self.page = FirstDollarLandingPage()

    def test_routes_discovery_to_authorized_soundcloud(self):
        self.assertEqual(self.page.links()["listen"], SOUNDCLOUD_DESTINATION)

    def test_merch_link_targets_authorized_store_with_attribution(self):
        shop = self.page.links("homepage")["shop"]
        self.assertTrue(shop.startswith(MERCH_DESTINATION))
        self.assertIn("utm_source=homepage", shop)
        self.assertIn("utm_medium=owned", shop)
        self.assertIn("utm_campaign=sonoxo-first-real-dollar", shop)
        self.assertIn("utm_content=first_dollar_primary", shop)

    def test_html_does_not_autoplay_or_claim_revenue(self):
        html = self.page.render().lower()
        self.assertNotIn("autoplay", html)
        self.assertNotIn("first $1 real revenue", html)
        self.assertIn("listen on soundcloud", html)
        self.assertIn("shop official merch", html)
        self.assertIn("no automated listening", html)


if __name__ == "__main__":
    unittest.main()

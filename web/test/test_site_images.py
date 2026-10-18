"""Regression coverage for images surviving the documentation build."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from mkdocs.exceptions import PluginError
from mkdocs_glightbox.plugin import LightboxPlugin


spec = importlib.util.spec_from_file_location(
    "site_hooks", Path(__file__).resolve().parents[1] / "site_hooks.py"
)
site_hooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_hooks)


class SiteImageTests(unittest.TestCase):
    def setUp(self):
        site_hooks.on_pre_build({})
        self.page = SimpleNamespace(
            file=SimpleNamespace(src_uri="sre/01-deploy-and-validate/index.md"),
            meta={},
        )

    def render(self, original, output):
        self.assertEqual(
            site_hooks.on_page_content(original, page=self.page), original
        )
        return site_hooks.on_post_page(output, page=self.page)

    def test_preserves_existing_and_new_images_inside_lightbox_links(self):
        original = '<p><img src="old.png"><img src="new.png"></p>'
        output = (
            '<html><body><a class="glightbox" href="old.png">'
            '<img src="old.png"></a><a class="glightbox" href="new.png">'
            '<img src="new.png"></a></body></html>'
        )
        self.assertEqual(self.render(original, output), output)

    def test_installed_lightbox_preserves_existing_and_new_images(self):
        plugin = LightboxPlugin()
        plugin.load_config({})
        config = {"theme": SimpleNamespace(name="material"), "plugins": {}}
        plugin.on_config(config)
        original = '<p><img src="old.png"><img src="new.png"></p>'
        output = plugin.on_page_content(original, page=self.page, config=config)
        self.assertIn('class="glightbox"', output)
        self.assertEqual(self.render(original, output), output)

    def test_rejects_all_images_being_removed(self):
        with self.assertRaisesRegex(PluginError, "old.png"):
            self.render('<img src="old.png"><img src="new.png">', "<p></p>")

    def test_rejects_one_missing_image_even_when_the_count_is_unchanged(self):
        with self.assertRaisesRegex(PluginError, "new.png"):
            self.render(
                '<img src="old.png"><img src="new.png">',
                '<img src="old.png"><img src="unrelated.png">',
            )

    def test_preserves_repeated_uses_of_the_same_image(self):
        with self.assertRaisesRegex(PluginError, "1 missing"):
            self.render(
                '<img src="same.png"><img src="same.png">', '<img src="same.png">'
            )

    def test_allows_theme_images_and_pages_without_screenshots(self):
        output = '<html><body><img src="logo.png"></body></html>'
        self.assertEqual(self.render("<p>No screenshots here.</p>", output), output)

    def test_decodes_escaped_image_urls(self):
        self.assertEqual(
            site_hooks.image_sources('<img src="image.png?a=1&amp;b=2" />'),
            {"image.png?a=1&b=2": 1},
        )

    def test_tracks_each_page_separately(self):
        other_page = SimpleNamespace(file=SimpleNamespace(src_uri="other.md"))
        site_hooks.on_page_content('<img src="one.png">', page=self.page)
        site_hooks.on_page_content('<img src="two.png">', page=other_page)
        site_hooks.on_post_page('<img src="two.png">', page=other_page)
        site_hooks.on_post_page('<img src="one.png">', page=self.page)


if __name__ == "__main__":
    unittest.main()

"""Fail the site build if an HTML plugin removes a page's images."""

from collections import Counter
from html.parser import HTMLParser

from mkdocs.exceptions import PluginError
from mkdocs.plugins import event_priority


class ImageSources(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources: Counter[str] = Counter()

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            source = dict(attrs).get("src")
            if source:
                self.sources[source] += 1


def image_sources(html: str) -> Counter[str]:
    parser = ImageSources()
    parser.feed(html)
    parser.close()
    return parser.sources


_expected_images: dict[str, Counter[str]] = {}


def on_pre_build(config, **kwargs):
    _expected_images.clear()


@event_priority(100)
def on_page_content(html, *, page, **kwargs):
    _expected_images[page.file.src_uri] = image_sources(html)
    return html


@event_priority(-100)
def on_post_page(output, *, page, **kwargs):
    missing = _expected_images.pop(page.file.src_uri) - image_sources(output)
    if missing:
        sources = ", ".join(
            f"{source} ({count} missing)" for source, count in sorted(missing.items())
        )
        raise PluginError(
            f"Image rendering removed images from {page.file.src_uri}: {sources}. "
            "Check the image-processing plugins and their dependency versions."
        )
    return output

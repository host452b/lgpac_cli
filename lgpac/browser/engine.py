"""
playwright browser engine.
wraps browser lifecycle, screenshot capture, and page state extraction.
"""
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright

from lgpac.config import SiteConfig

logger = logging.getLogger("lgpac.browser")


class BrowserEngine:
    """
    managed playwright browser with debug screenshot support.

    usage:
        with BrowserEngine(config) as engine:
            page = engine.new_page()
            engine.navigate(page, "http://example.com")
            engine.screenshot(page, "home")
    """

    def __init__(self, config: Optional[SiteConfig] = None, headless: bool = True):
        self.config = config or SiteConfig()
        self.headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._screenshot_dir: Optional[Path] = None
        self._screenshot_count = 0

    def __enter__(self) -> "BrowserEngine":
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def start(self):
        self._playwright = sync_playwright().start()
        launch_args = {"headless": self.headless}

        # prefer system chrome, fall back to bundled chromium
        for channel in ("chrome", None):
            try:
                if channel:
                    launch_args["channel"] = channel
                self._browser = self._playwright.chromium.launch(**launch_args)
                tag = channel or "bundled"
                logger.info(f"browser started ({tag})")
                return
            except Exception as e:
                logger.debug(f"launch with channel={channel} failed: {e}")
                launch_args.pop("channel", None)

        raise RuntimeError("failed to launch any browser")

    def stop(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    def new_context(self, mobile: bool = True) -> BrowserContext:
        if not self._browser:
            raise RuntimeError("browser not started")

        viewport = {"width": 430, "height": 932} if mobile else {"width": 1280, "height": 900}
        return self._browser.new_context(
            viewport=viewport,
            user_agent=self.config.user_agent,
        )

    def new_page(self, mobile: bool = True) -> Page:
        ctx = self.new_context(mobile=mobile)
        return ctx.new_page()

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #

    def navigate(self, page: Page, url: str, wait: str = "networkidle", timeout: int = 30000):
        logger.debug(f"navigating to {url}")
        page.goto(url, wait_until=wait, timeout=timeout)
        self._auto_screenshot(page, "navigate")

    def wait_for_load(self, page: Page, seconds: float = 3.0):
        page.wait_for_timeout(int(seconds * 1000))

    # ------------------------------------------------------------------ #
    # screenshots (only in debug mode)
    # ------------------------------------------------------------------ #

    def screenshot(self, page: Page, name: str, force: bool = False) -> Optional[Path]:
        """take a screenshot. only runs if debug=True or force=True."""
        if not self.config.debug and not force:
            return None

        if not self._screenshot_dir:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._screenshot_dir = self.config.output_path / "screenshots" / ts
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)

        self._screenshot_count += 1
        filename = f"{self._screenshot_count:03d}_{name}.png"
        filepath = self._screenshot_dir / filename
        try:
            page.screenshot(path=str(filepath), full_page=True, timeout=10000)
        except Exception:
            # full_page can timeout on uni-app SPAs, fall back to viewport
            page.screenshot(path=str(filepath), full_page=False, timeout=10000)
        logger.debug(f"screenshot: {filepath}")
        return filepath

    def _auto_screenshot(self, page: Page, label: str):
        if self.config.debug:
            self.screenshot(page, label)

    # ------------------------------------------------------------------ #
    # page state extraction
    # ------------------------------------------------------------------ #

    def extract_page_meta(self, page: Page) -> Dict[str, Any]:
        """extract page metadata and structure."""
        return page.evaluate("""() => {
            const meta = {};
            meta.url = location.href;
            meta.title = document.title;
            meta.description = '';
            const descEl = document.querySelector('meta[name="description"]');
            if (descEl) meta.description = descEl.content;

            // visible text content (trimmed)
            meta.text_content = document.body.innerText.substring(0, 5000);

            // all links on page
            meta.links = [];
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.getAttribute('href');
                const text = a.innerText.trim().substring(0, 100);
                if (href && !href.startsWith('javascript:') && text) {
                    meta.links.push({href, text});
                }
            });

            // all clickable elements (buttons, cards)
            meta.clickables = [];
            document.querySelectorAll(
                'button, [role="button"], [class*="card"], [class*="item"]'
            ).forEach(el => {
                const text = el.innerText.trim().substring(0, 100);
                const tag = el.tagName.toLowerCase();
                const cls = el.className.toString().substring(0, 100);
                if (text && text.length > 1 && text.length < 200) {
                    meta.clickables.push({tag, class: cls, text});
                }
            });

            // images
            meta.images = [];
            document.querySelectorAll('img[src]').forEach(img => {
                const src = img.getAttribute('src');
                const alt = img.getAttribute('alt') || '';
                if (src && !src.startsWith('data:')) {
                    meta.images.push({src, alt});
                }
            });

            return meta;
        }""")

    def extract_dom_tree(self, page: Page, max_depth: int = 5) -> Dict[str, Any]:
        """extract a simplified DOM tree structure."""
        return page.evaluate("""(maxDepth) => {
            function walk(el, depth) {
                if (depth > maxDepth) return null;
                const tag = el.tagName ? el.tagName.toLowerCase() : '#text';
                const node = {tag};

                if (el.id) node.id = el.id;
                if (el.className && typeof el.className === 'string') {
                    const cls = el.className.trim();
                    if (cls) node.class = cls.substring(0, 80);
                }

                const text = el.childNodes.length === 1 && el.childNodes[0].nodeType === 3
                    ? el.childNodes[0].textContent.trim().substring(0, 100) : '';
                if (text) node.text = text;

                const children = [];
                for (const child of el.children || []) {
                    const c = walk(child, depth + 1);
                    if (c) children.push(c);
                }
                if (children.length > 0) node.children = children;

                return node;
            }
            return walk(document.body, 0);
        }""", max_depth)

    def find_navigation_targets(self, page: Page) -> List[Dict[str, Any]]:
        """find all elements that could navigate to a new page/view."""
        return page.evaluate("""() => {
            const targets = [];
            const seen = new Set();

            // links with meaningful href
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.getAttribute('href');
                const text = a.innerText.trim();
                if (!href || href === '#' || href.startsWith('javascript:')) return;
                if (!text || seen.has(href)) return;
                seen.add(href);
                targets.push({
                    type: 'link',
                    selector: `a[href="${CSS.escape(href)}"]`,
                    href,
                    text: text.substring(0, 80),
                });
            });

            // clickable cards with price info (show cards)
            document.querySelectorAll('[class*="card"], [class*="show"], [class*="item"]').forEach(el => {
                const text = el.innerText.trim();
                if (text.includes('¥') || text.includes('起')) {
                    const idx = Array.from(el.parentElement.children).indexOf(el);
                    targets.push({
                        type: 'show_card',
                        selector: null,
                        element_index: idx,
                        text: text.substring(0, 120),
                        parent_class: (el.parentElement.className || '').substring(0, 60),
                    });
                }
            });

            // tab/category buttons
            document.querySelectorAll('[class*="tab"], [class*="category"]').forEach(el => {
                const text = el.innerText.trim();
                if (text && text.length < 30 && !seen.has('tab:' + text)) {
                    seen.add('tab:' + text);
                    targets.push({
                        type: 'tab',
                        text: text,
                        selector: null,
                    });
                }
            });

            return targets;
        }""")

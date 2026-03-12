"""
recursive depth-first traversal of the website.
visits every navigable link/button, records page structure at each node.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Set
from urllib.parse import urlparse, urljoin

from playwright.sync_api import Page

from lgpac.config import SiteConfig
from lgpac.browser.engine import BrowserEngine
from lgpac.browser.actions import ActionLibrary

logger = logging.getLogger("lgpac.traversal")


class PageNode:
    """represents one page/view in the site tree."""

    def __init__(self, url: str, title: str = "", depth: int = 0, trigger: str = ""):
        self.url = url
        self.title = title
        self.depth = depth
        self.trigger = trigger
        self.meta: Dict[str, Any] = {}
        self.dom_tree: Optional[Dict] = None
        self.children: List["PageNode"] = []
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "url": self.url,
            "title": self.title,
            "depth": self.depth,
            "trigger": self.trigger,
        }
        if self.meta:
            result["text_preview"] = (self.meta.get("text_content") or "")[:300]
            result["link_count"] = len(self.meta.get("links", []))
            result["image_count"] = len(self.meta.get("images", []))
            result["clickable_count"] = len(self.meta.get("clickables", []))
        if self.dom_tree:
            result["has_dom_tree"] = True
        if self.error:
            result["error"] = self.error
        if self.children:
            result["children"] = [c.to_dict() for c in self.children]
        return result


class SiteTraverser:
    """
    DFS traversal of the target site.

    starts from the homepage and recursively visits:
    1. category tabs
    2. show cards
    3. navigation links
    4. bottom nav pages

    records page metadata, DOM structure, and screenshots (in debug mode).
    """

    def __init__(
        self,
        config: Optional[SiteConfig] = None,
        max_depth: int = 3,
        max_pages: int = 50,
    ):
        self.config = config or SiteConfig()
        self.max_depth = max_depth
        self.max_pages = max_pages
        self._visited: Set[str] = set()
        self._page_count = 0
        self._output_dir: Optional[Path] = None

    def traverse(self) -> PageNode:
        """run the full traversal. returns the root node of the site tree."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._output_dir = self.config.output_path / "traversal" / ts
        self._output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"starting traversal (max_depth={self.max_depth}, max_pages={self.max_pages})")

        with BrowserEngine(self.config, headless=True) as engine:
            page = engine.new_page()

            engine.navigate(page, self.config.base_url)
            engine.wait_for_load(page, 5)

            ActionLibrary.dismiss_popups(page)

            root = self._visit_page(engine, page, self.config.base_url, depth=0, trigger="root")

            # traverse bottom navigation pages
            self._traverse_bottom_nav(engine, page, root)

            page.context.close()

        self._save_results(root)
        logger.info(f"traversal complete: {self._page_count} pages visited")
        return root

    def _visit_page(
        self,
        engine: BrowserEngine,
        page: Page,
        url: str,
        depth: int,
        trigger: str,
    ) -> PageNode:
        """visit a single page and extract its structure."""
        node = PageNode(url=url, depth=depth, trigger=trigger)

        normalized = self._normalize_url(url)
        if normalized in self._visited:
            node.error = "already_visited"
            return node

        if self._page_count >= self.max_pages:
            node.error = "max_pages_reached"
            return node

        if depth > self.max_depth:
            node.error = "max_depth_reached"
            return node

        self._visited.add(normalized)
        self._page_count += 1

        try:
            node.title = page.title() or ""
            node.meta = engine.extract_page_meta(page)
            node.dom_tree = engine.extract_dom_tree(page, max_depth=4)

            engine.screenshot(page, f"d{depth}_{self._page_count:03d}_{trigger[:20]}")

            logger.info(
                f"[{self._page_count}/{self.max_pages}] depth={depth} "
                f"url={url[:80]} links={len(node.meta.get('links', []))}"
            )

            # find and visit child pages
            if depth < self.max_depth:
                self._traverse_children(engine, page, node)

        except Exception as e:
            node.error = str(e)
            logger.warning(f"error visiting {url}: {e}")

        return node

    def _traverse_children(self, engine: BrowserEngine, page: Page, parent: PageNode):
        """find navigable targets on current page and visit them via DFS."""
        targets = engine.find_navigation_targets(page)
        current_url = page.url

        # prioritize: tabs first, then show cards, then links
        tabs = [t for t in targets if t["type"] == "tab"]
        cards = [t for t in targets if t["type"] == "show_card"]
        links = [t for t in targets if t["type"] == "link"]

        # visit tabs (they change content without navigation)
        for tab in tabs[:6]:
            if self._page_count >= self.max_pages:
                break
            self._visit_tab(engine, page, parent, tab)

        # visit show cards (click to navigate to detail)
        for card in cards[:6]:
            if self._page_count >= self.max_pages:
                break
            self._visit_clickable(engine, page, parent, card, current_url)

        # visit internal links
        for link in links[:10]:
            if self._page_count >= self.max_pages:
                break
            href = link.get("href", "")
            if not self._is_internal(href):
                continue
            full_url = urljoin(current_url, href)
            if self._normalize_url(full_url) in self._visited:
                continue
            self._visit_link(engine, page, parent, full_url, link.get("text", ""), current_url)

    def _visit_tab(self, engine: BrowserEngine, page: Page, parent: PageNode, tab: Dict):
        """click a category tab and record the resulting view."""
        tab_text = tab.get("text", "")
        if not tab_text:
            return

        url_before = page.url
        success = ActionLibrary.click_by_text(page, tab_text)
        if not success:
            logger.debug(f"tab click skipped: {tab_text}")
            return

        engine.wait_for_load(page, 2.5)

        virtual_url = f"{url_before}#tab={tab_text}"
        child = PageNode(
            url=virtual_url,
            title=tab_text,
            depth=parent.depth + 1,
            trigger=f"tab:{tab_text}",
        )

        try:
            child.meta = engine.extract_page_meta(page)
            engine.screenshot(page, f"tab_{tab_text}")
            self._page_count += 1
            self._visited.add(self._normalize_url(virtual_url))
            logger.info(f"  tab: {tab_text} ({len(child.meta.get('clickables', []))} clickables)")
        except Exception as e:
            child.error = str(e)

        parent.children.append(child)

    def _visit_clickable(
        self, engine: BrowserEngine, page: Page, parent: PageNode,
        card: Dict, fallback_url: str,
    ):
        """click a show card and visit the resulting detail page."""
        card_text = card.get("text", "")[:40]
        url_before = page.url

        # try clicking by a portion of the card text
        first_line = card_text.split("\n")[0].strip()
        if not first_line:
            return

        success = ActionLibrary.click_by_text(page, first_line, exact=False)
        if not success:
            return

        engine.wait_for_load(page, 3)
        ActionLibrary.dismiss_popups(page)
        url_after = page.url

        if ActionLibrary.is_same_page(url_before, url_after):
            return

        child = self._visit_page(
            engine, page, url_after,
            depth=parent.depth + 1,
            trigger=f"card:{first_line}",
        )
        parent.children.append(child)

        ActionLibrary.go_back_safe(page, fallback_url)
        engine.wait_for_load(page, 2)
        ActionLibrary.dismiss_popups(page)

    def _visit_link(
        self, engine: BrowserEngine, page: Page, parent: PageNode,
        url: str, text: str, fallback_url: str,
    ):
        """navigate to a link and visit the page."""
        try:
            engine.navigate(page, url, wait="domcontentloaded")
            engine.wait_for_load(page, 3)
            ActionLibrary.dismiss_popups(page)
        except Exception as e:
            logger.warning(f"failed to navigate to {url}: {e}")
            ActionLibrary.go_back_safe(page, fallback_url)
            return

        child = self._visit_page(
            engine, page, page.url,
            depth=parent.depth + 1,
            trigger=f"link:{text}",
        )
        parent.children.append(child)

        ActionLibrary.go_back_safe(page, fallback_url)
        engine.wait_for_load(page, 2)
        ActionLibrary.dismiss_popups(page)

    def _traverse_bottom_nav(self, engine: BrowserEngine, page: Page, root: PageNode):
        """visit each bottom navigation page."""
        nav_paths = [
            ("全部", "/pages/all/all"),
            ("周边", "/pages/shop/shop"),
        ]
        for name, path in nav_paths:
            if self._page_count >= self.max_pages:
                break
            url = f"{self.config.base_url}/#{path}"
            if self._normalize_url(url) in self._visited:
                continue

            try:
                engine.navigate(page, url, wait="domcontentloaded")
                engine.wait_for_load(page, 3)
                ActionLibrary.dismiss_popups(page)

                child = self._visit_page(
                    engine, page, page.url,
                    depth=1,
                    trigger=f"nav:{name}",
                )
                root.children.append(child)
            except Exception as e:
                logger.warning(f"failed to visit nav '{name}': {e}")

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.netloc}{path}"

    def _is_internal(self, href: str) -> bool:
        if not href:
            return False
        if href.startswith(("#", "/")):
            return True
        parsed = urlparse(href)
        base_host = urlparse(self.config.base_url).netloc
        return parsed.netloc in ("", base_host)

    def _save_results(self, root: PageNode):
        if not self._output_dir:
            return

        tree_file = self._output_dir / "site_tree.json"
        with open(tree_file, "w", encoding="utf-8") as f:
            json.dump(root.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"site tree saved to {tree_file}")

        # save flat list of all visited pages for easy querying
        flat = []
        self._flatten(root, flat)
        flat_file = self._output_dir / "all_pages.json"
        with open(flat_file, "w", encoding="utf-8") as f:
            json.dump(flat, f, ensure_ascii=False, indent=2)
        logger.info(f"flat page list saved to {flat_file} ({len(flat)} pages)")

    def _flatten(self, node: PageNode, result: List[Dict]):
        entry = {
            "url": node.url,
            "title": node.title,
            "depth": node.depth,
            "trigger": node.trigger,
        }
        if node.error:
            entry["error"] = node.error
        if node.meta:
            entry["text_preview"] = (node.meta.get("text_content") or "")[:200]
        result.append(entry)
        for child in node.children:
            self._flatten(child, result)

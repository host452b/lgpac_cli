"""
smart browser action library.
deterministic, non-AI actions for common automation patterns.
"""
import logging
from typing import Optional, List, Dict, Any

from playwright.sync_api import Page, TimeoutError as PwTimeout

logger = logging.getLogger("lgpac.actions")


class ActionLibrary:
    """
    reusable browser actions for SPA ticketing sites.
    all actions are deterministic - no AI involved.
    """

    # known popup patterns on this site
    POPUP_DISMISS_SELECTORS = [
        "text=简体中文",
        "text=确认",
        "text=确定",
        "text=我知道了",
        "text=关闭",
        "[class*='close']",
        "[class*='dismiss']",
        "[aria-label='close']",
        "[aria-label='Close']",
    ]

    @staticmethod
    def dismiss_popups(page: Page, timeout: int = 2000) -> bool:
        """try to close any visible popup/dialog. returns True if something was dismissed."""
        dismissed = False
        for selector in ActionLibrary.POPUP_DISMISS_SELECTORS:
            try:
                el = page.query_selector(selector)
                if not el:
                    continue
                # use force click for uni-app elements that may be "hidden"
                try:
                    el.click(force=True, timeout=1000)
                except Exception:
                    el.evaluate("e => e.click()")
                page.wait_for_timeout(500)
                dismissed = True
                logger.debug(f"dismissed popup via: {selector}")
            except Exception:
                continue
        return dismissed

    @staticmethod
    def smart_click(page: Page, selector: str, timeout: int = 5000) -> bool:
        """
        click an element, handling common obstacles:
        1. dismiss popups first
        2. scroll element into view
        3. wait for element to be clickable
        """
        ActionLibrary.dismiss_popups(page)

        try:
            page.wait_for_selector(selector, state="visible", timeout=timeout)
            el = page.query_selector(selector)
            if not el:
                return False

            el.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            el.click()
            return True
        except PwTimeout:
            logger.warning(f"element not found or not visible: {selector}")
            return False
        except Exception as e:
            logger.warning(f"click failed for {selector}: {e}")
            return False

    @staticmethod
    def click_by_text(page: Page, text: str, exact: bool = False, timeout: int = 5000) -> bool:
        """
        click an element by its visible text.
        falls back to JS click if playwright can't reach the element
        (common with uni-app hidden containers).
        """
        ActionLibrary.dismiss_popups(page)

        # try standard playwright click first
        try:
            locator = page.get_by_text(text, exact=exact)
            locator.first.wait_for(state="attached", timeout=timeout)
            locator.first.click(force=True)
            return True
        except Exception:
            pass

        # fallback: JS-based click for uni-app hidden containers
        try:
            clicked = page.evaluate("""(text) => {
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null
                );
                while (walker.nextNode()) {
                    if (walker.currentNode.textContent.trim().includes(text)) {
                        const el = walker.currentNode.parentElement;
                        if (el) { el.click(); return true; }
                    }
                }
                return false;
            }""", text)
            if clicked:
                page.wait_for_timeout(500)
                return True
        except Exception as e:
            logger.warning(f"click_by_text JS fallback failed for '{text}': {e}")

        return False

    @staticmethod
    def extract_text(page: Page, selector: str) -> Optional[str]:
        """extract text content from an element."""
        try:
            el = page.query_selector(selector)
            if el:
                return el.inner_text().strip()
        except Exception:
            pass
        return None

    @staticmethod
    def extract_all_text(page: Page, selector: str) -> List[str]:
        """extract text from all matching elements."""
        results = []
        try:
            elements = page.query_selector_all(selector)
            for el in elements:
                text = el.inner_text().strip()
                if text:
                    results.append(text)
        except Exception:
            pass
        return results

    @staticmethod
    def extract_fields(page: Page, field_map: Dict[str, str]) -> Dict[str, Optional[str]]:
        """extract multiple named fields by their selectors."""
        result = {}
        for name, selector in field_map.items():
            result[name] = ActionLibrary.extract_text(page, selector)
        return result

    @staticmethod
    def wait_for_navigation(page: Page, timeout: int = 10000):
        """wait for a navigation event to complete."""
        try:
            page.wait_for_load_state("networkidle", timeout=timeout)
        except PwTimeout:
            logger.debug("navigation wait timed out, continuing")

    @staticmethod
    def scroll_to_bottom(page: Page, step: int = 500, delay: int = 300):
        """scroll to bottom of page in steps (for lazy-loaded content)."""
        prev_height = 0
        while True:
            page.evaluate(f"window.scrollBy(0, {step})")
            page.wait_for_timeout(delay)
            current_height = page.evaluate("document.body.scrollHeight")
            scroll_pos = page.evaluate("window.scrollY + window.innerHeight")
            if scroll_pos >= current_height or current_height == prev_height:
                break
            prev_height = current_height

    @staticmethod
    def go_back_safe(page: Page, fallback_url: str = "") -> bool:
        """go back in history, with fallback to a URL if history is empty."""
        try:
            page.go_back(wait_until="domcontentloaded", timeout=10000)
            return True
        except Exception:
            if fallback_url:
                page.goto(fallback_url, wait_until="domcontentloaded", timeout=15000)
                return True
            return False

    @staticmethod
    def is_same_page(url_before: str, url_after: str) -> bool:
        """check if the URL actually changed (ignoring hash fragments)."""
        def strip_hash(u):
            return u.split("#")[0].rstrip("/")
        return strip_hash(url_before) == strip_hash(url_after)

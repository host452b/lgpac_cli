"""
deterministic replay engine.
executes YAML-defined playbooks for precise, repeatable browser automation.

no AI involved - pure step-by-step execution with smart fallbacks.

playbook format:
    name: check_show_detail
    description: open a specific show and extract info
    steps:
      - action: navigate
        url: http://example.com
      - action: dismiss_popup
      - action: wait
        seconds: 3
      - action: click_text
        text: "银河喜剧群星秀"
      - action: wait_for_load
      - action: screenshot
        name: show_detail
      - action: extract
        fields:
          title: ".show-name, h1"
          price: "[class*='price']"
      - action: assert_visible
        selector: "text=选座购票"
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

import yaml
from playwright.sync_api import Page

from lgpac.config import SiteConfig
from lgpac.browser.engine import BrowserEngine
from lgpac.browser.actions import ActionLibrary

logger = logging.getLogger("lgpac.replay")


class StepResult:
    def __init__(self, step_index: int, action: str, success: bool, data: Any = None, error: str = ""):
        self.step_index = step_index
        self.action = action
        self.success = success
        self.data = data
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "step": self.step_index,
            "action": self.action,
            "success": self.success,
        }
        if self.data is not None:
            d["data"] = self.data
        if self.error:
            d["error"] = self.error
        return d


class PlaybookRunner:
    """
    load and execute YAML playbooks.
    each step maps to a deterministic browser action.
    """

    def __init__(self, config: Optional[SiteConfig] = None):
        self.config = config or SiteConfig()
        self._results: List[StepResult] = []

    def run_file(self, playbook_path: str) -> List[StepResult]:
        """load a YAML playbook and execute it."""
        path = Path(playbook_path)
        if not path.exists():
            raise FileNotFoundError(f"playbook not found: {playbook_path}")

        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()

        # resolve __TARGET_URL__ placeholder
        raw = raw.replace("__TARGET_URL__", self.config.base_url)
        playbook = yaml.safe_load(raw)

        return self.run(playbook)

    def run(self, playbook: Dict[str, Any]) -> List[StepResult]:
        """execute a playbook dict."""
        name = playbook.get("name", "unnamed")
        steps = playbook.get("steps", [])
        if not steps:
            raise ValueError("playbook has no steps")

        logger.info(f"running playbook: {name} ({len(steps)} steps)")
        self._results = []

        with BrowserEngine(self.config, headless=True) as engine:
            page = engine.new_page()

            for i, step in enumerate(steps):
                action = step.get("action", "")
                optional = step.get("optional", False)

                logger.debug(f"step {i}: {action}")
                result = self._execute_step(engine, page, i, step)
                self._results.append(result)

                if not result.success and not optional:
                    on_fail = step.get("on_fail", "stop")
                    if on_fail == "stop":
                        logger.error(f"step {i} ({action}) failed: {result.error}")
                        break
                    elif on_fail == "continue":
                        logger.warning(f"step {i} ({action}) failed, continuing: {result.error}")

            page.context.close()

        success_count = sum(1 for r in self._results if r.success)
        logger.info(f"playbook '{name}' complete: {success_count}/{len(self._results)} steps ok")

        self._save_results(name)
        return self._results

    def _execute_step(self, engine: BrowserEngine, page: Page, index: int, step: Dict) -> StepResult:
        """dispatch a single step to the appropriate handler."""
        action = step.get("action", "")
        handlers = {
            "navigate": self._action_navigate,
            "wait": self._action_wait,
            "wait_for_load": self._action_wait_for_load,
            "dismiss_popup": self._action_dismiss_popup,
            "click": self._action_click,
            "click_text": self._action_click_text,
            "scroll_bottom": self._action_scroll_bottom,
            "screenshot": self._action_screenshot,
            "extract": self._action_extract,
            "extract_meta": self._action_extract_meta,
            "assert_visible": self._action_assert_visible,
            "assert_url_contains": self._action_assert_url,
            "go_back": self._action_go_back,
            "type_text": self._action_type_text,
        }

        handler = handlers.get(action)
        if not handler:
            return StepResult(index, action, False, error=f"unknown action: {action}")

        try:
            data = handler(engine, page, step)
            return StepResult(index, action, True, data=data)
        except Exception as e:
            return StepResult(index, action, False, error=str(e))

    # ------------------------------------------------------------------ #
    # action handlers
    # ------------------------------------------------------------------ #

    def _action_navigate(self, engine: BrowserEngine, page: Page, step: Dict) -> Any:
        url = step["url"]
        engine.navigate(page, url, wait="domcontentloaded")
        engine.wait_for_load(page, step.get("wait_after", 3))
        return {"url": page.url}

    def _action_wait(self, _engine: BrowserEngine, page: Page, step: Dict) -> Any:
        seconds = step.get("seconds", 2)
        page.wait_for_timeout(int(seconds * 1000))
        return None

    def _action_wait_for_load(self, engine: BrowserEngine, page: Page, step: Dict) -> Any:
        ActionLibrary.wait_for_navigation(page, step.get("timeout", 10000))
        return None

    def _action_dismiss_popup(self, _engine: BrowserEngine, page: Page, _step: Dict) -> Any:
        dismissed = ActionLibrary.dismiss_popups(page)
        return {"dismissed": dismissed}

    def _action_click(self, _engine: BrowserEngine, page: Page, step: Dict) -> Any:
        selector = step["selector"]
        success = ActionLibrary.smart_click(page, selector, step.get("timeout", 5000))
        if not success:
            raise RuntimeError(f"click failed: {selector}")
        page.wait_for_timeout(step.get("wait_after", 1000))
        return {"clicked": selector}

    def _action_click_text(self, _engine: BrowserEngine, page: Page, step: Dict) -> Any:
        text = step["text"]
        exact = step.get("exact", False)
        success = ActionLibrary.click_by_text(page, text, exact=exact)
        if not success:
            raise RuntimeError(f"click_text failed: {text}")
        page.wait_for_timeout(step.get("wait_after", 1000))
        return {"clicked_text": text}

    def _action_scroll_bottom(self, _engine: BrowserEngine, page: Page, _step: Dict) -> Any:
        ActionLibrary.scroll_to_bottom(page)
        return None

    def _action_screenshot(self, engine: BrowserEngine, page: Page, step: Dict) -> Any:
        name = step.get("name", "step")
        path = engine.screenshot(page, name, force=True)
        return {"path": str(path) if path else None}

    def _action_extract(self, _engine: BrowserEngine, page: Page, step: Dict) -> Any:
        fields = step.get("fields", {})
        return ActionLibrary.extract_fields(page, fields)

    def _action_extract_meta(self, engine: BrowserEngine, page: Page, _step: Dict) -> Any:
        return engine.extract_page_meta(page)

    def _action_assert_visible(self, _engine: BrowserEngine, page: Page, step: Dict) -> Any:
        selector = step["selector"]
        el = page.query_selector(selector)
        if not el or not el.is_visible():
            raise AssertionError(f"element not visible: {selector}")
        return {"visible": True}

    def _action_assert_url(self, _engine: BrowserEngine, page: Page, step: Dict) -> Any:
        expected = step["contains"]
        actual = page.url
        if expected not in actual:
            raise AssertionError(f"URL '{actual}' does not contain '{expected}'")
        return {"url": actual}

    def _action_go_back(self, _engine: BrowserEngine, page: Page, step: Dict) -> Any:
        fallback = step.get("fallback_url", self.config.base_url)
        ActionLibrary.go_back_safe(page, fallback)
        return {"url": page.url}

    def _action_type_text(self, _engine: BrowserEngine, page: Page, step: Dict) -> Any:
        selector = step["selector"]
        text = step["text"]
        page.fill(selector, text)
        return {"typed": text}

    # ------------------------------------------------------------------ #
    # results
    # ------------------------------------------------------------------ #

    def _save_results(self, name: str):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = self.config.output_path / "replay" / ts
        output_dir.mkdir(parents=True, exist_ok=True)

        results_file = output_dir / f"{name}_results.json"
        data = [r.to_dict() for r in self._results]
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"replay results saved to {results_file}")

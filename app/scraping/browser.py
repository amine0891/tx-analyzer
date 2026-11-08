"""Selenium renderer for permitted pages, with an optional human-in-the-loop mode."""

import logging
import os
import re
import time
from collections.abc import Callable
from html import unescape
from xml.etree import ElementTree

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)


class BrowserRenderer:
    def __init__(self, manual: bool = False) -> None:
        self._driver: webdriver.Chrome | None = None
        self.manual = manual
        self.manual_wait_seconds = int(os.getenv("MANUAL_BROWSER_TIMEOUT_SECONDS", "900"))

    def _start(self) -> webdriver.Chrome:
        options = Options()
        options.binary_location = os.getenv("CHROME_BIN", "/usr/bin/chromium")
        if not self.manual:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1364,900")
        options.page_load_strategy = "eager"
        driver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(20)
        self._driver = driver
        return driver

    def _navigate(self, driver: webdriver.Chrome, url: str) -> None:
        try:
            driver.get(url)
        except TimeoutException:
            if not self.manual:
                raise
            logger.warning(
                "Le chargement du document dépasse le délai Selenium; "
                "le navigateur semi-automatique reste ouvert pour la validation humaine : %s",
                url,
            )

    def _wait_for_manual_page(
        self,
        driver: webdriver.Chrome,
        url: str,
        ready_check: Callable[[str], bool],
    ) -> str:
        deadline = time.monotonic() + self.manual_wait_seconds
        html = driver.page_source
        if ready_check(html):
            return html
        logger.warning(
            "Le navigateur semi-automatique attend une page exploitable pour %s. "
            "Si un contrôle humain apparaît, ouvrez http://localhost:7900/vnc.html?autoconnect=1&resize=scale "
            "et terminez-le manuellement; le collecteur ne le contourne pas.",
            url,
        )
        while time.monotonic() < deadline:
            html = driver.page_source
            if ready_check(html):
                return html
            time.sleep(1)
        raise TimeoutError(
            f"La page n'est pas devenue exploitable après {self.manual_wait_seconds} secondes : {url}"
        )

    def render(
        self,
        url: str,
        ready_check: Callable[[str], bool] | None = None,
    ) -> str:
        driver = self._driver or self._start()
        self._navigate(driver, url)
        if self.manual and ready_check is not None:
            return self._wait_for_manual_page(driver, url, ready_check)
        WebDriverWait(driver, 10, poll_frequency=0.25).until(
            lambda current: current.execute_script("return document.readyState")
            in {"interactive", "complete"}
        )
        try:
            WebDriverWait(driver, 8, poll_frequency=0.25).until(
                lambda current: len(
                    current.find_element(By.TAG_NAME, "body").text.strip()
                ) >= 200
            )
        except Exception:
            # Return the rendered DOM anyway; the caller classifies empty or blocked pages.
            pass
        return driver.page_source

    def render_listing_with_load_more(
        self,
        url: str,
        article_path_pattern: str,
        button_text: str,
        target_count: int = 2000,
        max_clicks: int = 260,
    ) -> str:
        """Expand a public listing through its normal "load more" control."""
        driver = self._driver or self._start()
        self._navigate(driver, url)
        WebDriverWait(driver, 10, poll_frequency=0.25).until(
            lambda current: current.execute_script("return document.readyState")
            in {"interactive", "complete"}
        )

        def count_article_links() -> int:
            return int(driver.execute_script(
                """
                const pattern = new RegExp(arguments[0]);
                const paths = new Set();
                for (const anchor of document.querySelectorAll('a[href]')) {
                    try {
                        const path = new URL(anchor.href).pathname;
                        if (pattern.test(path)) paths.add(path);
                    } catch (_) {}
                }
                return paths.size;
                """,
                article_path_pattern,
            ))

        lowered = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        upper_xpath = "abcdefghijklmnopqrstuvwxyz"
        button_query = (
            "//*[self::button or @role='button']"
            f"[contains(translate(normalize-space(.), '{lowered}', '{upper_xpath}'), "
            f"'{button_text.lower()}')]"
        )
        clicks = 0
        for _ in range(max_clicks):
            current_count = count_article_links()
            if current_count >= target_count:
                break
            buttons = driver.find_elements(By.XPATH, button_query)
            button = next(
                (candidate for candidate in buttons if candidate.is_displayed() and candidate.is_enabled()),
                None,
            )
            if button is None:
                break
            button.click()
            clicks += 1
            try:
                WebDriverWait(driver, 12, poll_frequency=0.25).until(
                    lambda _: count_article_links() > current_count
                )
            except TimeoutException:
                logger.warning(
                    "Le contrôle '%s' n'a pas chargé de nouveaux articles sur %s",
                    button_text,
                    url,
                )
                break

        found = count_article_links()
        logger.info(
            "Liste GameSpot développée : %s liens d'article après %s clic(s), cible=%s",
            found,
            clicks,
            target_count,
        )
        return driver.page_source

    @staticmethod
    def _sitemap_data(driver: webdriver.Chrome) -> tuple[str, list[str]]:
        # Selenium's page_source preserves the XML document more reliably than
        # querying Chrome's built-in XML viewer DOM, which may wrap XML nodes.
        page_source = driver.page_source
        try:
            root = ElementTree.fromstring(page_source.encode("utf-8"))
            root_name = root.tag.rsplit("}", 1)[-1].lower()
            locations = [
                node.text.strip()
                for node in root.iter()
                if node.tag.rsplit("}", 1)[-1].lower() == "loc"
                and node.text and node.text.strip()
            ]
            if root_name in {"urlset", "sitemapindex"}:
                return root_name, locations
        except ElementTree.ParseError:
            pass

        # Chromium can serialize its XML viewer as an HTML wrapper. Recover the
        # original sitemap tags from that serialized document before DOM fallback.
        sources = [page_source]
        try:
            visible_text = driver.execute_script(
                "return document.body ? document.body.innerText : ''"
            )
            if visible_text:
                sources.append(str(visible_text))
        except Exception:
            pass
        for source_text in sources:
            root_match = re.search(
                r"<(?:[\w.-]+:)?(urlset|sitemapindex)\b",
                source_text,
                flags=re.IGNORECASE,
            )
            if root_match is None:
                continue
            locations = [
                unescape(re.sub(r"<[^>]+>", "", value)).strip()
                for value in re.findall(
                    r"<(?:[\w.-]+:)?loc\b[^>]*>(.*?)</(?:[\w.-]+:)?loc\s*>",
                    source_text,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            ]
            locations = [location for location in locations if location]
            if locations:
                return root_match.group(1).lower(), locations

        result = driver.execute_script(
            """
            const root = document.documentElement;
            const name = (root && root.localName ? root.localName : '').toLowerCase();
            const locations = Array.from(document.getElementsByTagNameNS('*', 'loc'))
                .map(node => (node.textContent || '').trim())
                .filter(Boolean);
            return {name, locations};
            """
        )
        return str(result.get("name", "")), list(result.get("locations", []))

    def read_sitemap(self, url: str) -> tuple[str, list[str]]:
        """Read a sitemap in the visible browser, pausing for human validation if needed."""
        driver = self._driver or self._start()
        self._navigate(driver, url)
        deadline = time.monotonic() + (self.manual_wait_seconds if self.manual else 20)
        announced = False
        while time.monotonic() < deadline:
            root_name, locations = self._sitemap_data(driver)
            if root_name in {"urlset", "sitemapindex"}:
                logger.info(
                    "Sitemap XML lu dans le navigateur : type=%s, entrées=%s, URL=%s",
                    root_name,
                    len(locations),
                    url,
                )
                return root_name, locations
            if self.manual and not announced:
                announced = True
                logger.warning(
                    "Le sitemap n'est pas encore accessible dans le navigateur. "
                    "S'il affiche un contrôle humain, résolvez-le dans l'onglet ouvert; "
                    "le collecteur attend sans l'automatiser."
                )
            time.sleep(1)
        raise TimeoutError(f"Aucun sitemap exploitable n'a été chargé : {url}")

    def close(self) -> None:
        if self._driver is not None:
            self._driver.quit()
            self._driver = None

import asyncio
import gzip
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from pymongo import UpdateOne
from pymongo.database import Database

from app.core.config import settings
from app.db.repository import save_article
from app.schemas import Article
from app.scraping.browser import BrowserRenderer
from app.scraping.sources import SOURCES, Source

logger = logging.getLogger(__name__)

ARTICLE_SELECTORS = (
    "[itemprop='articleBody']",
    ".article-body",
    ".article__body",
    ".article-content",
    ".content-body",
    ".news-body",
    "article",
)
MIN_ARTICLE_TEXT_LENGTH = 200
ARTICLE_TYPES = {"article", "newsarticle", "reportagenewsarticle", "blogposting"}
ASSET_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif", ".pdf",
    ".mp4", ".m3u8", ".mp3", ".xml", ".gz",
)
MAX_RETRIES = 3
MAX_MANUAL_LISTING_PAGES_PER_PATH = 20


def _same_site(url: str, source: Source) -> bool:
    candidate = urlparse(url)
    base = urlparse(source.base_url)
    if candidate.scheme.lower() not in {"http", "https"}:
        return False
    candidate_host = (candidate.hostname or "").lower().removeprefix("www.")
    source_host = (base.hostname or "").lower().removeprefix("www.")
    return candidate_host == source_host


def _can_fetch(robots: RobotFileParser, source: Source, url: str) -> bool:
    """Honor robots.txt unless this source has explicit collection permission."""
    if source.robots_permission_confirmed:
        return True
    return robots.can_fetch("TXAnalyzer", url)


def _sitemap_roots(robots: RobotFileParser, source: Source) -> list[str]:
    sitemap_urls = robots.site_maps() or []
    if not sitemap_urls:
        sitemap_urls = [urljoin(source.base_url, "/sitemap.xml")]
    source_path = urlparse(source.base_url).path.strip("/")
    language = source_path.split("/", 1)[0] if source_path else ""
    if language in {"ar", "en", "es", "fr"}:
        language_maps = [url for url in sitemap_urls if f"/sitemaps/{language}/" in urlparse(url).path]
        if language_maps:
            sitemap_urls = language_maps
    return list(dict.fromkeys(url.split("#", 1)[0] for url in sitemap_urls))


def _xml_root_and_locs(content: bytes, url: str) -> tuple[str, list[str]]:
    if urlparse(url).path.lower().endswith(".gz"):
        content = gzip.decompress(content)
    root = ElementTree.fromstring(content)
    root_type = root.tag.rsplit("}", 1)[-1].lower()
    locations = [
        node.text.strip()
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1].lower() == "loc" and node.text and node.text.strip()
    ]
    return root_type, locations


def _is_asset_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(ASSET_SUFFIXES)


def _is_article_candidate(url: str, source: Source) -> bool:
    """Apply the publisher's URL shape and known archive/page exclusions."""
    if not _same_site(url, source) or _is_asset_url(url):
        return False
    parsed = urlparse(url)
    path = parsed.path
    if path in {"", "/"} or not re.search(source.article_path_pattern, path):
        return False
    query = parse_qs(parsed.query)
    if any(
        key.lower() in {"p", "page", "paged"}
        and any(value.isdigit() for value in values)
        for key, values in query.items()
    ):
        return False
    return not any(re.search(pattern, path) for pattern in source.non_article_path_patterns)


def _article_section(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.split("/", 1)[0] if path else "_root"


def _backfill_sections(database: Database, collection_name: str, source: Source) -> None:
    """Add a first-path segment to existing URLs for reporting and fair selection."""
    collection = getattr(database, collection_name)
    updates = []
    cursor = collection.find(
        {"source_id": source.source_id, "section": {"$exists": False}},
        {"url": 1},
    ).batch_size(1000)
    for job in cursor:
        updates.append(
            UpdateOne(
                {"_id": job["_id"]},
                {"$set": {"section": _article_section(job["url"])}},
            )
        )
        if len(updates) >= 500:
            collection.bulk_write(updates, ordered=False)
            updates.clear()
    if updates:
        collection.bulk_write(updates, ordered=False)


def _contains_access_block(html: str) -> bool:
    lowered = html.lower()
    markers = (
        "cf-chl-", "challenge-form", "captcha", "access denied",
        "verify you are human", "just a moment", "enable javascript and cookies to continue",
        "403 forbidden", "request blocked", "access has been denied", "robot check",
    )
    return any(marker in lowered for marker in markers)


def _article_selectors(source: Source | None = None) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*(source.article_selectors if source else ()), *ARTICLE_SELECTORS)))


def _looks_js_rendered(html: str, url: str, source: Source | None = None) -> bool:
    """Only offer Selenium a permitted, article-shaped page with a JS-shell signature."""
    if _contains_access_block(html):
        return False
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    if len(path_parts) < 2:
        return False

    soup = BeautifulSoup(html, "html.parser")
    body_text = soup.body.get_text(" ", strip=True) if soup.body else ""
    if len(body_text) >= 2000:
        return False
    shell_markers = (
        "__NEXT_DATA__", "__NUXT__", "data-reactroot", "webpack", "hydrateRoot",
    )
    has_shell = (
        any(marker.lower() in html.lower() for marker in shell_markers)
        or soup.select_one("#root, #app, #__next, [data-reactroot]") is not None
    )
    has_article_metadata = soup.select_one(
        "meta[property='og:type'][content='article'], script[type='application/ld+json']"
    ) is not None
    has_heading = soup.select_one("h1") is not None
    article_text_length = max(
        (len(node.get_text(" ", strip=True)) for selector in _article_selectors(source) for node in soup.select(selector)),
        default=0,
    )
    missing_article_body = article_text_length < MIN_ARTICLE_TEXT_LENGTH
    return missing_article_body and (has_shell or (has_article_metadata and not has_heading))


def _enqueue(database: Database, collection: str, document: dict[str, object]) -> bool:
    result = getattr(database, collection).update_one(
        {"url": document["url"]},
        {"$setOnInsert": document},
        upsert=True,
    )
    return result.upserted_id is not None


def _set_queue_status(database: Database, collection: str, url: str, **values: object) -> None:
    getattr(database, collection).update_one({"url": url}, {"$set": values})


def _json_ld_articles(data: object) -> list[dict[str, object]]:
    """Return Article-like objects from JSON-LD, including @graph entries."""
    if isinstance(data, list):
        return [article for item in data for article in _json_ld_articles(item)]
    if not isinstance(data, dict):
        return []

    articles: list[dict[str, object]] = []
    types = data.get("@type", [])
    if isinstance(types, str):
        types = [types]
    elif not isinstance(types, list):
        types = []
    if any(str(item).lower() in ARTICLE_TYPES for item in types):
        articles.append(data)
    for key in ("@graph", "mainEntity", "mainEntityOfPage"):
        articles.extend(_json_ld_articles(data.get(key)))
    return articles


def _clean_article(html: str, url: str = "", source: Source | None = None) -> tuple[str, str] | None:
    soup = BeautifulSoup(html, "html.parser")

    # Prefer structured article data when the publisher embeds it in the
    # document response; some pages expose this more reliably than CSS classes.
    for script in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("JSON-LD mal formé ignoré sur %s : %s", url, exc)
            continue
        for article in _json_ld_articles(payload):
            title = str(article.get("headline") or article.get("name") or "").strip()
            body = article.get("articleBody")
            if isinstance(body, str):
                content = re.sub(r"\s+", " ", body).strip()
                if title and len(content) >= MIN_ARTICLE_TEXT_LENGTH:
                    return title[:500], content

    for node in soup.select("script, style, nav, footer, header, aside, form"):
        node.decompose()
    title_node = soup.select_one("h1")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    if not title:
        title_node = soup.select_one("meta[property='og:title']")
        title = title_node.get("content", "").strip() if title_node else ""

    candidates = [node for selector in _article_selectors(source) for node in soup.select(selector)]
    if not candidates:
        candidates = [node for node in soup.select("main")]
    content_node = max(candidates, key=lambda node: len(node.get_text(" ", strip=True)), default=None)
    if not title or content_node is None:
        return None

    content = re.sub(r"\s+", " ", content_node.get_text(" ", strip=True)).strip()
    if len(content) < MIN_ARTICLE_TEXT_LENGTH:
        return None
    return title[:500], content


async def _get_html(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    if "html" not in response.headers.get("content-type", "").lower():
        raise ValueError("La réponse n'est pas une page HTML")
    return response.text


async def _robots_policy(client: httpx.AsyncClient, source: Source) -> tuple[RobotFileParser, float] | None:
    robots_url = urljoin(source.base_url, "/robots.txt")
    parser = RobotFileParser(robots_url)
    try:
        response = await client.get(robots_url)
        if response.status_code == 429:
            logger.warning("robots.txt renvoie 429 pour %s; la source est suspendue", source.name)
            return None
        if 400 <= response.status_code < 500:
            # RFC 9309 section 2.3.1.3 treats a 4xx robots.txt as unavailable;
            # the crawler may access resources, but must still honor any
            # restrictions or blocks returned by the actual sitemap/article URLs.
            logger.warning(
                "robots.txt indisponible (%s) pour %s; règles absentes selon RFC 9309",
                response.status_code,
                source.name,
            )
            parser.parse([])
            return parser, settings.request_delay_seconds
        response.raise_for_status()
        parser.parse(response.text.splitlines())
        crawl_delay = parser.crawl_delay("TXAnalyzer") or 0
        return parser, max(settings.request_delay_seconds, float(crawl_delay))
    except Exception:
        logger.exception("Impossible de verifier robots.txt pour %s; le site est ignore", source.name)
        return None


async def _discover_from_listings(
    client: httpx.AsyncClient,
    database: Database,
    source: Source,
    robots: RobotFileParser,
    delay: float,
    browser: BrowserRenderer | None = None,
    counts: dict[str, int] | None = None,
    max_candidates: int = 2000,
) -> int:
    """Use source-specific public pages when a sitemap is unavailable or empty."""
    queued = 0
    visited_pages: set[str] = set()

    def enqueue_links(html: str, listing_url: str) -> int:
        discovered = 0
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a[href]"):
            article_url = urljoin(listing_url, anchor.get("href", "")).split("#", 1)[0]
            if _is_article_candidate(article_url, source) and _can_fetch(robots, source, article_url):
                discovered += int(_enqueue(database, "article_queue", {
                    "url": article_url,
                    "section": _article_section(article_url),
                    "source_id": source.source_id,
                    "status": "pending",
                    "attempts": 0,
                    "discovered_at": datetime.now(timezone.utc),
                }))
        return discovered

    def has_candidate_links(html: str, listing_url: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a[href]"):
            article_url = urljoin(listing_url, anchor.get("href", "")).split("#", 1)[0]
            if _is_article_candidate(article_url, source) and _can_fetch(robots, source, article_url):
                return True
        return False

    def pagination_links(html: str, listing_url: str, base_path: str) -> list[str]:
        if not base_path:
            return []
        found: list[str] = []
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a[href]"):
            page_url = urljoin(listing_url, anchor.get("href", "")).split("#", 1)[0]
            parsed = urlparse(page_url)
            if not _same_site(page_url, source):
                continue
            if not (parsed.path.rstrip("/") == base_path or parsed.path.startswith(base_path + "/")):
                continue
            query = parse_qs(parsed.query)
            is_page = any(
                key.lower() in {"p", "page", "paged"}
                and any(value.isdigit() for value in values)
                for key, values in query.items()
            )
            if is_page and page_url not in visited_pages and page_url not in found:
                found.append(page_url)
        return found

    for path in source.listing_paths:
        initial_url = urljoin(source.base_url, path)
        if not _same_site(initial_url, source):
            continue
        base_path = urlparse(initial_url).path.rstrip("/")
        page_limit = MAX_MANUAL_LISTING_PAGES_PER_PATH if source.collection_mode == "manual" else 1
        pending_pages = [initial_url]
        processed_pages = 0
        while pending_pages and processed_pages < page_limit:
            listing_url = pending_pages.pop(0)
            if listing_url in visited_pages or not _can_fetch(robots, source, listing_url):
                continue
            visited_pages.add(listing_url)
            processed_pages += 1
            if delay > 0:
                await asyncio.sleep(delay)
            html: str | None = None
            try:
                response = await client.get(listing_url)
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", "").lower():
                    logger.info("Page de découverte ignorée (non HTML) : %s", listing_url)
                    continue
                html = response.text
                if not has_candidate_links(html, listing_url) and browser is not None and (
                    browser.manual or not _contains_access_block(html)
                ):
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if counts is not None:
                        counts["browser_fallbacks"] += 1
                    ready_check = (
                        lambda candidate: has_candidate_links(candidate, listing_url)
                        if browser.manual else None
                    )
                    rendered_html = await asyncio.to_thread(browser.render, listing_url, ready_check)
                    if not _contains_access_block(rendered_html):
                        html = rendered_html
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 403 or browser is None:
                    logger.warning(
                        "Page de découverte refusée par la source (%s) : %s",
                        exc.response.status_code,
                        listing_url,
                    )
                    continue
                mode = "visible semi-automatique" if browser.manual else "Selenium"
                logger.info("Reprise %s après HTTP 403 sur la page publique : %s", mode, listing_url)
                try:
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if counts is not None:
                        counts["browser_fallbacks"] += 1
                    ready_check = (
                        lambda candidate: has_candidate_links(candidate, listing_url)
                        if browser.manual else None
                    )
                    rendered_html = await asyncio.to_thread(browser.render, listing_url, ready_check)
                    if _contains_access_block(rendered_html):
                        logger.warning("La page de découverte affiche un blocage; arrêt sur %s", listing_url)
                        continue
                    html = rendered_html
                except Exception:
                    logger.exception("Échec du rendu navigateur de la page %s", listing_url)
                    continue
            except Exception:
                logger.exception("Échec de la page de découverte %s (%s)", listing_url, source.name)
                continue

            if html is None:
                continue
            if source.listing_load_more_text and browser is not None:
                if delay > 0:
                    await asyncio.sleep(delay)
                if counts is not None:
                    counts["browser_fallbacks"] += 1
                try:
                    expanded_html = await asyncio.to_thread(
                        browser.render_listing_with_load_more,
                        listing_url,
                        source.article_path_pattern,
                        source.listing_load_more_text,
                        max_candidates,
                    )
                    if _contains_access_block(expanded_html):
                        logger.warning(
                            "La liste GameSpot affiche un blocage; conservation de la réponse HTML initiale.",
                        )
                    else:
                        html = expanded_html
                except Exception:
                    logger.exception(
                        "Impossible de développer la liste GameSpot; utilisation des liens HTML déjà reçus : %s",
                        listing_url,
                    )
            queued += enqueue_links(html, listing_url)
            for next_page in pagination_links(html, listing_url, base_path):
                if next_page not in pending_pages:
                    pending_pages.append(next_page)
    return queued


async def _discover_from_feeds(
    client: httpx.AsyncClient,
    database: Database,
    source: Source,
    robots: RobotFileParser,
    delay: float,
) -> tuple[int, int]:
    """Read explicitly configured RSS/Atom feeds, including full-content feeds."""
    queued = 0
    discovered = 0
    for path in source.feed_paths:
        feed_url = urljoin(source.base_url, path)
        if not _same_site(feed_url, source) or not _can_fetch(robots, source, feed_url):
            continue
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            response = await client.get(feed_url)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
            entries = [
                node for node in root.iter()
                if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
            ]
            for entry in entries:
                fields: dict[str, list[str]] = {}
                for child in entry.iter():
                    name = child.tag.rsplit("}", 1)[-1].split(":")[-1].lower()
                    value = (child.text or "").strip()
                    if value:
                        fields.setdefault(name, []).append(value)
                title = next(iter(fields.get("title", [])), "").strip()
                link = next(iter(fields.get("link", [])), "").strip()
                if not link:
                    link_node = next(
                        (node for node in entry if node.tag.rsplit("}", 1)[-1].lower() == "link"),
                        None,
                    )
                    link = str(link_node.attrib.get("href", "")).strip() if link_node is not None else ""
                if not title or not _is_article_candidate(link, source):
                    continue
                html_candidates = fields.get("encoded", []) + fields.get("content", []) + fields.get("description", []) + fields.get("summary", [])
                if not html_candidates:
                    continue
                body_html = max(html_candidates, key=len)
                body_soup = BeautifulSoup(body_html, "html.parser")
                for node in body_soup.select("script, style, nav, footer, header, aside, form"):
                    node.decompose()
                content = re.sub(r"\s+", " ", body_soup.get_text(" ", strip=True)).strip()
                if len(content) < MIN_ARTICLE_TEXT_LENGTH:
                    continue
                discovered += 1
                document = {
                    "url": link,
                    "section": _article_section(link),
                    "source_id": source.source_id,
                    "status": "pending",
                    "attempts": 0,
                    "feed_title": title[:500],
                    "feed_content": content,
                    "discovered_at": datetime.now(timezone.utc),
                }
                added = _enqueue(database, "article_queue", document)
                if added:
                    queued += 1
                else:
                    database.article_queue.update_one(
                        {
                            "url": link,
                            "source_id": source.source_id,
                            "status": {"$in": ["pending", "error", "skipped"]},
                        },
                        {"$set": {
                            "feed_title": document["feed_title"],
                            "feed_content": content,
                            "status": "pending",
                        }},
                    )
            logger.info("Flux traité pour %s : %s entrée(s) d'article", source.name, discovered)
        except Exception:
            logger.exception("Échec du flux public %s (%s)", feed_url, source.name)
    return queued, discovered


async def collect_source(
    client: httpx.AsyncClient,
    database: Database,
    source: Source,
    max_articles: int | None = None,
    max_sitemaps: int | None = None,
    browser: BrowserRenderer | None = None,
    sitemaps_only: bool = False,
    max_total_articles: int | None = 2000,
) -> dict[str, int]:
    counts = {
        "sitemaps_queued": 0, "sitemaps_processed": 0,
        "articles_queued": 0, "saved": 0, "skipped": 0, "errors": 0,
        "browser_fallbacks": 0, "articles_filtered": 0,
        "manual_required": 0, "disabled": 0, "limit_reached": 0,
    }
    saved_before = database.articles.count_documents({"source_id": source.source_id})
    effective_total_cap = max_total_articles
    if effective_total_cap is not None:
        remaining_articles = effective_total_cap - saved_before
        if remaining_articles <= 0:
            counts["limit_reached"] = 1
            logger.info(
                "Plafond atteint pour %s : %s/%s articles déjà enregistrés; source ignorée.",
                source.name, saved_before, effective_total_cap,
            )
            return counts
        max_articles = remaining_articles if max_articles is None else min(max_articles, remaining_articles)
    if source.collection_mode == "disabled":
        counts["disabled"] = 1
        logger.warning("Source désactivée : %s", source.disabled_reason or source.name)
        return counts
    if source.collection_mode == "manual" and (browser is None or not browser.manual):
        counts["manual_required"] = 1
        logger.info("La source %s attend le collecteur semi-automatique visible", source.name)
        return counts
    try:
        policy = await _robots_policy(client, source)
        if policy is None:
            return {**counts, "errors": 1}
        robots, delay = policy
        if not _can_fetch(robots, source, source.base_url):
            logger.warning("robots.txt interdit la collecte de %s", source.name)
            return counts
    except Exception:
        logger.exception("Impossible de lire robots.txt pour %s", source.name)
        return {**counts, "errors": 1}

    now = datetime.now(timezone.utc)
    sitemap_roots = [url for url in _sitemap_roots(robots, source) if _same_site(url, source)]
    if not sitemap_roots:
        logger.warning("Aucun sitemap du même site n'est déclaré pour %s", source.name)
        return counts

    database.sitemap_queue.update_many(
        {"source_id": source.source_id, "status": "error", "attempts": {"$lt": MAX_RETRIES}},
        {"$set": {"status": "pending"}},
    )
    database.article_queue.update_many(
        {"source_id": source.source_id, "status": "error", "attempts": {"$lt": MAX_RETRIES}},
        {"$set": {"status": "pending"}},
    )
    if browser is not None and browser.manual:
        # A prior headless challenge is not evidence that this human-assisted
        # pass should give up; let the visible browser retry those checkpoints.
        database.sitemap_queue.update_many(
            {"source_id": source.source_id, "status": "error"},
            {"$set": {"status": "pending", "attempts": 0}},
        )
        database.article_queue.update_many(
            {"source_id": source.source_id, "status": {"$in": ["error", "blocked"]}},
            {"$set": {"status": "pending", "attempts": 0}},
        )
    database.sitemap_queue.update_many(
        {"source_id": source.source_id, "status": "blocked"},
        {"$set": {"status": "pending"}},
    )
    # A revised source profile may turn previously rejected paths into valid
    # article candidates (for example, a newly discovered video/news route).
    for job in database.article_queue.find(
        {
            "source_id": source.source_id,
            "status": "skipped",
            "last_error": "URL de rubrique ou format non-article pour cette source",
        },
        {"url": 1},
    ):
        if _is_article_candidate(job["url"], source):
            database.article_queue.update_one(
                {"_id": job["_id"]},
                {"$set": {"status": "pending"}, "$unset": {"last_error": ""}},
            )

    # Existing sitemap jobs predate the priority field; backfill only once.
    for job in database.sitemap_queue.find(
        {"source_id": source.source_id, "status": "pending", "priority": {"$exists": False}},
        {"url": 1},
    ):
        priority = int(not re.search(source.priority_sitemap_pattern, job["url"]))
        database.sitemap_queue.update_one({"_id": job["_id"]}, {"$set": {"priority": priority}})
    for sitemap_url in sitemap_roots:
        priority = int(not re.search(source.priority_sitemap_pattern, sitemap_url))
        added = _enqueue(database, "sitemap_queue", {
            "url": sitemap_url,
            "source_id": source.source_id,
            "status": "pending",
            "attempts": 0,
            "is_root": True,
            "priority": priority,
            "discovered_at": now,
        })
        counts["sitemaps_queued"] += int(added)
        # Re-read top-level sitemap files on every invocation to discover new
        # daily/news maps; completed child maps remain checkpointed.
        database.sitemap_queue.update_one(
            {"url": sitemap_url, "source_id": source.source_id, "status": "done"},
            {"$set": {"status": "pending"}},
        )

    sitemaps_attempted = 0
    article_sitemap_seen = False
    while True:
        if max_sitemaps is not None and sitemaps_attempted >= max_sitemaps:
            break
        sitemap_job = database.sitemap_queue.find_one(
            {"source_id": source.source_id, "status": "pending"},
            sort=[("is_root", -1), ("priority", 1), ("url", -1)],
        )
        if sitemap_job is None:
            break
        sitemaps_attempted += 1
        sitemap_url = sitemap_job["url"]
        if not _can_fetch(robots, source, sitemap_url):
            counts["skipped"] += 1
            _set_queue_status(database, "sitemap_queue", sitemap_url, status="blocked")
            logger.warning("Sitemap interdit par robots.txt : %s", sitemap_url)
            continue
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            response = await client.get(sitemap_url)
            try:
                response.raise_for_status()
                root_type, locations = _xml_root_and_locs(response.content, sitemap_url)
            except (httpx.HTTPStatusError, ValueError, ElementTree.ParseError, OSError) as exc:
                status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if not (
                    browser is not None and browser.manual
                    and source.collection_mode == "manual"
                    and (status_code == 403 or isinstance(exc, (ValueError, ElementTree.ParseError, OSError)))
                ):
                    raise
                counts["browser_fallbacks"] += 1
                logger.info("Ouverture visible du sitemap refusé pour validation humaine : %s", sitemap_url)
                root_type, locations = await asyncio.to_thread(browser.read_sitemap, sitemap_url)
            if root_type == "sitemapindex":
                for location in locations:
                    child_url = urljoin(sitemap_url, location).split("#", 1)[0]
                    if _same_site(child_url, source):
                        added = _enqueue(database, "sitemap_queue", {
                            "url": child_url,
                            "source_id": source.source_id,
                            "status": "pending",
                            "attempts": 0,
                            "is_root": False,
                            "priority": int(not re.search(source.priority_sitemap_pattern, child_url)),
                            "discovered_at": now,
                        })
                        counts["sitemaps_queued"] += int(added)
            elif root_type == "urlset":
                article_sitemap_seen = True
                for location in locations:
                    article_url = urljoin(sitemap_url, location).split("#", 1)[0]
                    if _is_article_candidate(article_url, source):
                        added = _enqueue(database, "article_queue", {
                            "url": article_url,
                            "section": _article_section(article_url),
                            "source_id": source.source_id,
                            "status": "pending",
                            "attempts": 0,
                            "discovered_at": now,
                        })
                        counts["articles_queued"] += int(added)
                    elif _same_site(article_url, source) and not _is_asset_url(article_url):
                        counts["articles_filtered"] += 1
            else:
                raise ValueError(f"Racine XML inattendue : {root_type}")
            counts["sitemaps_processed"] += 1
            _set_queue_status(
                database, "sitemap_queue", sitemap_url, status="done",
                checked_at=datetime.now(timezone.utc), attempts=0, last_error=None,
            )
        except Exception as exc:
            attempts = int(sitemap_job.get("attempts", 0)) + 1
            _set_queue_status(
                database, "sitemap_queue", sitemap_url,
                status="error", attempts=attempts, last_error=str(exc)[:500],
            )
            counts["errors"] += 1
            logger.exception("Échec HTTP ou XML pour le sitemap %s (%s)", sitemap_url, source.name)

    has_pending_articles = database.article_queue.find_one(
        {"source_id": source.source_id, "status": "pending"}, {"_id": 1}
    ) is not None
    if counts["errors"] or not article_sitemap_seen or not has_pending_articles:
        feed_queued, feed_discovered = await _discover_from_feeds(
            client, database, source, robots, delay,
        )
        counts["articles_queued"] += feed_queued
        if feed_discovered == 0 or source.listing_load_more_text:
            counts["articles_queued"] += await _discover_from_listings(
                client, database, source, robots, delay, browser, counts,
                # Discover a reserve beyond the save cap so rejected or
                # malformed pages do not leave the source short of 2,000.
                max_candidates=max(2000, (max_total_articles or 2000) * 2),
            )
    if sitemaps_only:
        return counts

    _backfill_sections(database, "article_queue", source)
    _backfill_sections(database, "articles", source)
    sections = sorted(database.article_queue.distinct(
        "section", {"source_id": source.source_id, "status": "pending"}
    ))
    section_cursor = 0
    processed = 0
    while True:
        if max_articles is not None and processed >= max_articles:
            break
        article_job = None
        if sections:
            for offset in range(len(sections)):
                section_index = (section_cursor + offset) % len(sections)
                section = sections[section_index]
                article_job = database.article_queue.find_one(
                    {"source_id": source.source_id, "status": "pending", "section": section},
                    sort=[("url", -1)],
                )
                if article_job is not None:
                    section_cursor = (section_index + 1) % len(sections)
                    break
        if article_job is None:
            break
        url = article_job["url"]
        if not _is_article_candidate(url, source):
            counts["articles_filtered"] += 1
            _set_queue_status(
                database, "article_queue", url,
                status="skipped", last_error="URL de rubrique ou format non-article pour cette source",
            )
            continue
        processed += 1
        if not _can_fetch(robots, source, url):
            counts["skipped"] += 1
            _set_queue_status(database, "article_queue", url, status="blocked")
            continue
        if database.articles.find_one({"url": url}, {"_id": 1}):
            counts["skipped"] += 1
            database.article_queue.update_one(
                {"url": url},
                {"$set": {"status": "done", "attempts": 0, "last_error": None},
                 "$unset": {"feed_title": "", "feed_content": ""}},
            )
            continue
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            feed_content = str(article_job.get("feed_content", "")).strip()
            if feed_content:
                article_data = (str(article_job.get("feed_title", "")).strip(), feed_content)
            else:
                try:
                    html = await _get_html(client, url)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 403 or browser is None:
                        raise
                    counts["browser_fallbacks"] += 1
                    mode = "visible semi-automatique" if browser.manual else "Selenium"
                    logger.info("Reprise %s après HTTP 403 sur l'article public : %s", mode, url)
                    ready_check = (
                        lambda candidate: _clean_article(candidate, url, source) is not None
                        if browser.manual else None
                    )
                    rendered_html = await asyncio.to_thread(browser.render, url, ready_check)
                    if _contains_access_block(rendered_html):
                        counts["skipped"] += 1
                        _set_queue_status(
                            database, "article_queue", url,
                            status="blocked", last_error="Écran de blocage détecté avec le navigateur",
                        )
                        logger.warning("Le rendu navigateur affiche un blocage; arrêt sur %s", url)
                        continue
                    html = rendered_html
                article_data = _clean_article(html, url, source)
            if article_data is None and _looks_js_rendered(html, url, source):
                if delay > 0:
                    await asyncio.sleep(delay)
                counts["browser_fallbacks"] += 1
                logger.info("Repli Selenium pour une page probablement rendue en JavaScript : %s", url)
                if browser is None:
                    raise RuntimeError("Le rendu Selenium n'est pas configure")
                ready_check = (
                    lambda candidate: _clean_article(candidate, url, source) is not None
                    if browser.manual else None
                )
                rendered_html = await asyncio.to_thread(browser.render, url, ready_check)
                if _contains_access_block(rendered_html):
                    counts["skipped"] += 1
                    _set_queue_status(database, "article_queue", url, status="blocked")
                    logger.warning("Page de blocage détectée après rendu navigateur : %s", url)
                    continue
                article_data = _clean_article(rendered_html, url, source)
            elif (
                article_data is None
                and browser is not None
                and browser.manual
                and source.collection_mode == "manual"
            ):
                # A 200 response can still be an interstitial or an empty JS shell.
                # The visible browser waits for a genuinely parseable article DOM.
                counts["browser_fallbacks"] += 1
                logger.info("Repli navigateur semi-automatique pour l'article : %s", url)
                rendered_html = await asyncio.to_thread(
                    browser.render,
                    url,
                    lambda candidate: _clean_article(candidate, url, source) is not None,
                )
                article_data = _clean_article(rendered_html, url, source)
            if article_data is None:
                counts["skipped"] += 1
                _set_queue_status(database, "article_queue", url, status="skipped")
                logger.info("Contenu insuffisant ou structure inconnue : %s", url)
                continue
            title, content = article_data
            article = Article(
                url=url,
                title=title,
                content=content,
                source_id=source.source_id,
                section=_article_section(url),
                collected_at=datetime.now(timezone.utc),
            )
            inserted = save_article(database, article)
            counts["saved"] += int(inserted)
            counts["skipped"] += int(not inserted)
            database.article_queue.update_one(
                {"url": url},
                {"$set": {"status": "done", "attempts": 0, "last_error": None},
                 "$unset": {"feed_title": "", "feed_content": ""}},
            )
        except Exception as exc:
            attempts = int(article_job.get("attempts", 0)) + 1
            _set_queue_status(
                database, "article_queue", url,
                status="error", attempts=attempts, last_error=str(exc)[:500],
            )
            counts["errors"] += 1
            logger.exception("Échec HTTP ou parsing de l'article %s (%s)", url, source.name)
    return counts


async def collect_all(
    database: Database,
    sources: tuple[Source, ...] = SOURCES,
    max_articles: int | None = None,
    max_sitemaps: int | None = None,
    sitemaps_only: bool = False,
    manual_browser: bool = False,
    max_total_articles: int | None = 2000,
) -> dict[str, dict[str, int]]:
    timeout = httpx.Timeout(20, connect=10)
    headers = {"User-Agent": "TXAnalyzer/0.1 (educational data collection)"}
    browser = BrowserRenderer(manual=manual_browser)
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        results = {}
        try:
            for source in sources:
                results[source.source_id] = await collect_source(
                    client, database, source, max_articles, max_sitemaps, browser,
                    sitemaps_only, max_total_articles,
                )
        finally:
            await asyncio.to_thread(browser.close)
    return results

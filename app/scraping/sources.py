from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    source_id: str
    name: str
    base_url: str
    listing_paths: tuple[str, ...] = ("/",)
    article_path_pattern: str = r".+"
    article_selectors: tuple[str, ...] = ()
    non_article_path_patterns: tuple[str, ...] = ()
    priority_sitemap_pattern: str = r"(?!)"
    feed_paths: tuple[str, ...] = ()
    collection_mode: str = "automatic"
    disabled_reason: str | None = None
    robots_permission_confirmed: bool = False
    listing_load_more_text: str | None = None


SOURCES = (
    Source("humanite", "L'Humanité", "https://www.humanite.fr/", ("/",),
           r"/(?:politique|societe|social-et-economie|monde|culture-et-savoir|sport|en-debat|environnement|sciences|medias|histoire|feminisme|vie-quotidienne)/.+",
           ("[itemprop='articleBody']", ".article-body", ".article-content"),
           (r"/(?:rubriques?|recherche|tag|tags|auteur|authors?|page|podcasts?)(?:/|$)",),
           r"/(?:news|article)[^/]*\.xml$"),
    Source("gamespot", "GameSpot", "https://www.gamespot.com/", ("/category/news/",),
           r"/articles?/[^?#]+",
           ("[itemprop='articleBody']", ".article__body", ".js-article-body", "#article-content-body"),
           (r"/(?:category|tag|tags|search|author|video|videos)(?:/|$)",),
           feed_paths=("/feeds/mashup/",), listing_load_more_text="load more articles"),
    Source("marianne", "Marianne", "https://www.marianne.net/",
           ("/", "/politique/", "/societe/", "/monde/", "/economie/", "/culture/", "/medias/", "/environnement/", "/idees/"),
           r"/(?:politique|societe|monde|economie|culture|medias|environnement|idees|debats?)/.+",
           ("[itemprop='articleBody']", ".article-body", ".article-content", ".post-content"),
           (r"/(?:rubriques?|recherche|tag|tags|auteur|authors?|page|podcasts?|videos?)(?:/|$)",),
           collection_mode="manual"),
    Source("lemonde", "Le Monde", "https://www.lemonde.fr/", ("/",),
           r"/(?:article|politique|international|societe|planete|economie|culture|idees|sport|pixels|m-le-mag)/.+",
           ("[itemprop='articleBody']", ".article__content", ".article__content-single"),
           (r"/(?:rubriques?|recherche|tag|tags|auteur|authors?|page|podcasts?|videos?)(?:/|$)",),
           r"/sitemap_news\.xml$"),
    Source("france24", "France 24", "https://www.france24.com/fr/", ("/fr/",),
           r"/fr/[^/]+/[^/]+/?$",
           ("[itemprop='articleBody']", ".article__body", ".t-content__body"),
           (r"/fr/(?:direct|emissions|programmes|recherche)/?$",),
           r"/news\.xml$"),
    Source("france3-regions", "France 3 Régions", "https://france3-regions.franceinfo.fr/", ("/",),
           r"/(?:[a-z0-9-]+/){2,}[^/]+/?$",
           ("[itemprop='articleBody']", ".article-content", ".article__content"),
           (r"/(?:recherche|tag|tags|auteur|authors?|communes?|villes?|page|videos?)(?:/|$)",)),
    Source("mediacites", "Mediacites", "https://www.mediacites.fr/", ("/",),
           r"/(?:enquete|la-fabrique|lyon|lille|nantes|toulouse|bordeaux|strasbourg|grenoble|montpellier|rouen|marseille|nimes|limoges|metz|reims|perpignan|la-reunion)/[^/]+/20\d{2}/\d{2}/\d{2}/[^/]+/?$",
           ("[itemprop='articleBody']", ".entry-content", ".article-content"),
           (r"/(?:mandat|recherche|tag|tags|author|auteur|page|podcasts?|videos?)(?:/|$)",),
           r"/post-sitemap(?:\d+)?\.xml$"),
    Source("lepoint", "Le Point", "https://www.lepoint.fr/", ("/",),
           r"/.+-[A-Z0-9]{15,}/?$",
           ("[itemprop='articleBody']", ".article__content", ".article-content"),
           (r"/(?:rubriques?|recherche|tag|tags|auteur|authors?|page|podcasts?|videos?)(?:/|$)",),
           collection_mode="manual",
           robots_permission_confirmed=True),
)


SOURCES_BY_ID = {source.source_id: source for source in SOURCES}

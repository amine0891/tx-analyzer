# Exécution, configuration et dépannage

[Accueil](../README.md) · [Collecte](collecte.md) · [Notebook](playwright.md)

Toutes les commandes s’exécutent dans PowerShell à la racine du dépôt. Copier `.env.example` vers `.env` une seule fois, puis lancer `docker compose up --build -d`. Consulter les services avec `docker compose ps` et les journaux avec `docker compose logs --tail 100 api mongo`.

## Découverte et collecte

```powershell
docker compose up -d mongo

# Découvrir les URL sans télécharger le contenu final des articles
docker compose --profile scraping run --build --rm scraper python scripts/collect.py --source humanite --sitemaps-only --max-sitemaps 20

# Poursuivre et traiter dix URL au plus
docker compose --profile scraping run --rm scraper python scripts/collect.py --source humanite --max-sitemaps 20 --max-articles 10

# Six sources automatiques avec plafond cumulatif par source
docker compose --profile scraping run --rm scraper python scripts/collect.py --exclude-source marianne --exclude-source lepoint --max-total-articles-per-source 2000

docker compose --profile scraping run --rm scraper python scripts/collect.py --help
```

Relancer une commande réutilise les files et ignore les articles déjà enregistrés. Les limites sont par source : `--max-sitemaps` porte sur les fichiers tentés par passage, `--max-articles` sur les jobs traités, `--max-total-articles-per-source` sur le total enregistré.

## Navigateur visible

Marianne et Le Point utilisent ce parcours; GameSpot est aussi accepté par le lanceur :

```powershell
docker compose --profile manual run --build --service-ports --rm manual-collector python scripts/collect_manual.py --source marianne --max-sitemaps 1 --max-articles 1
```

Pendant l’exécution, ouvrir `http://localhost:7900/vnc.html?autoconnect=1&resize=scale`. Le navigateur attend les étapes humaines éventuelles; consulter [les profils](collecte.md) avant de réutiliser une source.

## Collecte périodique

```powershell
$env:COLLECT_INTERVAL_SECONDS = "86400"
docker compose --profile crawler up --build -d worker
docker compose logs -f worker
```

Le worker lance un cycle immédiatement puis attend l’intervalle : une journée par défaut, minimum 60 secondes. Il lance seulement les sources automatiques et conserve le plafond cumulatif de 2 000 articles par source. Arrêt : `docker compose --profile crawler stop worker`.

## Analyse et graphique

```powershell
docker compose exec api python scripts/analyze.py
docker compose exec api python scripts/analyze.py --source lepoint
docker compose exec api python scripts/generate_dashboard.py
```

Une analyse ciblée actualise les prédictions sans créer un nouvel instantané agrégé. Relancer l’analyse globale avant le graphique. Le générateur vérifie que les huit sources ont un instantané de même date et des compteurs cohérents avec les prédictions. Le PNG est créé dans `output/toxicite_par_site.png`.

## Configuration

| Variable | Défaut | Rôle |
| --- | --- | --- |
| APP_NAME / APP_ENV | TX Analyzer / development | Métadonnées |
| MONGODB_URI | mongodb://localhost:27017 | Python local; Compose impose l’hôte réseau mongo |
| MONGODB_DATABASE | tx_analyzer | Base |
| HOST / PORT | 0.0.0.0 / 8000 | Adresse et port; Compose impose l’hôte interne |
| REQUEST_DELAY_SECONDS | 0.5 | Délai minimal, augmenté selon robots.txt |
| TOXICITY_LIGHT_THRESHOLD | 0.5 | Seuil inférieur |
| TOXICITY_HIGH_THRESHOLD | 0.75 | Seuil supérieur |

Les seuils doivent respecter `0 ≤ léger < élevé ≤ 1`. Garder `.env` conforme au modèle : les clés inconnues sont rejetées. Exporter dans PowerShell les paramètres propres à Compose tels que `COLLECT_INTERVAL_SECONDS` et `MANUAL_BROWSER_TIMEOUT_SECONDS` (900 secondes par défaut).

## Dépannage

| Symptôme | Vérification |
| --- | --- |
| Santé 503 / MongoDB indisponible | État des services et journaux mongo |
| Première prédiction lente | Téléchargement des poids, connexion et journaux API |
| Source ignorée : manual_required | Utiliser le lanceur manual selon la source |
| Aucun nouvel article | Plafond cumulatif, files en attente, blocages et filtres |
| Graphique refusé | Terminer l’analyse globale après la collecte |
| Port 8000 occupé | Changer PORT dans .env, relancer et adapter les URL |
| Configuration : extra inputs | Retirer les anciennes clés inconnues du .env |

`docker compose down` arrête les services ordinaires en conservant les volumes MongoDB et le cache modèle.

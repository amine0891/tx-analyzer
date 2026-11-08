# TX Analyzer

Pipeline Python qui collecte des articles de presse, estime leur niveau de toxicité avec **Detoxify** et conserve les articles, prédictions et statistiques dans **MongoDB**. Le projet expose aussi une API REST pour analyser un texte à la demande.

> Projet de démonstration Data Engineering : collecte, traitement NLP, persistance et exposition des résultats dans une API.

## Ce que fait le projet

- Collecte des articles à partir des sitemaps et pages de sources configurées dans `app/scraping/sources.py`.
- Analyse locale des textes avec le modèle multilingue Detoxify.
- Classement en trois niveaux : non toxique, légèrement toxique ou fortement toxique.
- Stockage des articles, prédictions et statistiques par source dans MongoDB.
- API FastAPI avec documentation OpenAPI interactive.
- Services conteneurisés avec Docker Compose.

## Architecture

```text
app/
  api/        Endpoints FastAPI
  core/       Configuration de l'application
  db/         Connexion et persistance MongoDB
  ml/         Classification Detoxify
  scraping/   Sources, collecte et rendu navigateur
scripts/
  collect.py          Collecte automatique
  collect_manual.py   Collecte assistée par navigateur
  analyze.py          Classification et statistiques par source
  worker.py           Collecte planifiée
  generate_dashboard.py Génération du graphique des statistiques
```

## Démarrage rapide

### Prérequis

- Docker et Docker Compose
- Une connexion Internet au premier démarrage pour télécharger l'image MongoDB et les poids du modèle

### Lancer l'API et MongoDB

Depuis la racine du projet :

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

L'API est disponible sur `http://localhost:8000`. Consultez la documentation interactive sur `http://localhost:8000/docs` et l'état des services sur `http://localhost:8000/health`.

### Envoyer une prédiction

```powershell
curl.exe -X POST http://localhost:8000/predict `
  -H "Content-Type: application/json" `
  -d '{"text":"Exemple de texte à analyser","source_url":"https://example.org/article"}'
```

La réponse contient `prediction`, `toxicity_level` et `score`. La prédiction est enregistrée dans MongoDB.

### Lancer une collecte de démonstration

La collecte est un traitement séparé de l'API. Pour un premier essai limité :

```powershell
docker compose --profile scraping run --build --rm scraper python scripts/collect.py --max-sitemaps 2 --max-articles 1
```

Les sources, limites et options du collecteur sont définies par le code et les arguments de `scripts/collect.py`. Respectez les conditions d'utilisation des sites et configurez une collecte raisonnable avant toute exécution à grande échelle.

## Configuration

Copiez `.env.example` vers `.env` pour personnaliser les paramètres locaux. Les principaux réglages couvrent le nom de la base MongoDB, le port de l'API, le délai entre les requêtes et les seuils de classification. Le fichier `.env` est ignoré par Git ; ne publiez jamais de secret ni de données collectées.

Le cache des poids du modèle est conservé dans un volume Docker. Les textes de prédiction sont traités localement par le conteneur et ne sont pas transmis à une API de classification externe.

## Technologies

Python 3.12 · FastAPI · Detoxify · MongoDB · Docker Compose · BeautifulSoup · HTTPX · Selenium

## État du projet

Version de démonstration en évolution. Le comportement de la collecte dépend de la disponibilité et des règles d'accès de chaque source. Les résultats du modèle sont des estimations automatiques et doivent être interprétés avec prudence.

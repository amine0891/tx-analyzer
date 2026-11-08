# API, scores et données

[Accueil](../README.md) · [Commandes](utilisation.md)

## Contrat REST

| Route | Fonction | Code |
| --- | --- | --- |
| GET /health | Ping MongoDB et état de service | [routes.py](../app/api/routes.py) |
| POST /predict | Classifier et enregistrer le texte | [routes.py](../app/api/routes.py) |
| GET /docs | Documentation OpenAPI | [main.py](../app/main.py) |

Requête :

```json
{"text":"Texte à analyser","source_url":"https://example.org/article"}
```

`text` est requis, limité à 20 000 caractères et ne peut être vide après nettoyage des espaces périphériques. `source_url` est une URL HTTP optionnelle. Les modèles sont dans [schemas.py](../app/schemas.py).

Réponse illustrative, sans exécution du modèle :

```json
{"prediction":"non_toxic","toxicity_level":"non_toxic","score":0.9}
```

Une requête invalide produit une erreur 422. Le point de santé renvoie 503 si MongoDB ne répond pas. La prédiction renvoie 503 si le modèle ou l’enregistrement échoue. La santé ne vérifie pas le chargement du modèle.

## Lire les scores

[classifier.py](../app/ml/classifier.py) charge `Detoxify("multilingual")` à la demande une seule fois par processus.

| Score brut toxicity | Niveau par défaut |
| --- | --- |
| < 0.5 | non_toxic |
| ≥ 0.5 et < 0.75 | slightly_toxic |
| ≥ 0.75 | highly_toxic |

Le champ API `score` vaut `toxicity` pour une décision toxique, et `1 - toxicity` sinon, arrondi à six décimales. Il ne correspond donc pas toujours au score brut de toxicité. Le score `severe_toxicity` est conservé en interne, mais ne détermine pas les niveaux.

## Collections MongoDB

Les index sont créés dans [repository.py](../app/db/repository.py).

| Collection | Contenu | Déduplication |
| --- | --- | --- |
| sitemap_queue | URL XML, source, état, priorité et tentatives | URL unique |
| article_queue | URL candidate, source, rubrique, état et tentatives | URL unique |
| articles | URL, titre, texte, source, rubrique et date; résultats après analyse | URL unique et upsert |
| predictions | Texte, résultat, date; article_id pour les traitements par article | Index unique sparse sur article_id |
| site_statistics | Source, date du passage, nombre classé et pourcentages | Index par source et date |

L’analyse réutilise les résultats compatibles avec les seuils actuels, actualise l’article et fait un upsert de sa prédiction liée à `article_id`. Les appels API sont stockés séparément sans identifiant d’article.

## Calcul des statistiques

[analyze.py](../scripts/analyze.py) traite des lots de 16 textes et peut reprendre article par article lorsqu’un lot échoue. Les pourcentages portent sur les articles disposant d’un résultat exploitable : `100 × nombre du niveau / nombre total classé`. Un compteur à zéro peut indiquer une absence de données.

L’analyse globale enregistre les statistiques avec une même date de passage. Stabiliser la collecte pendant l’analyse facilite l’interprétation de cet instantané; ces résultats ne constituent pas un échantillonnage représentatif des médias.

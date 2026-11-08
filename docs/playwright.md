# Notebook Playwright

[Accueil](../README.md) · [Notebook](../lepoint.ipynb) · [Collecteur intégré](collecte.md)

## Installer

Utiliser Python 3.12 pour rester aligné sur le conteneur. L’extra optionnel installe JupyterLab et Playwright :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[notebook]"
```

Le notebook appelle `chromium.connect_over_cdp("http://localhost:9222")` et attend un navigateur Chromium déjà ouvert avec le débogage distant. Installer Playwright seul ne lance pas ce navigateur.

Exemple PowerShell pour Chrome installé au chemin standard, à adapter :

```powershell
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$profile = Join-Path $PWD ".browser-profile"
Start-Process -FilePath $chrome -ArgumentList "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=9222", "--user-data-dir=`"$profile`""
.\.venv\Scripts\python.exe -m jupyterlab lepoint.ipynb
```

Le profil dédié est exclu de Git; garder le point CDP local. Vérifier CDP_URL, URL de départ, cibles, délais et fichiers de sortie avant exécution.

## Sections et fonctions

| Section | Fonctions à lire | Parcours |
| --- | --- | --- |
| Diagnostic Le Point | diag_articles() | Inspection des liens probables sur une rubrique |
| Collecte Le Point | collect_all_urls(), extract_from_fusion(), main_async() | Rubriques et chargement supplémentaire → lepoint_articles.jsonl |
| Collecte GameSpot | collect_from_rss(), collect_from_category_with_load_more(), scrape_gamespot() | RSS puis catégories → gamespot_articles.jsonl |
| Import MongoDB | import_jsonl(), flush_batch(), main() | JSONL choisi → upserts dans articles |

Le Point utilise notamment `window.Fusion.globalContent` pour lire le contenu. GameSpot combine les flux RSS et catégories puis extrait les champs des pages. Les cibles sont de 2 000 URL pour Le Point et 800 pour GameSpot; elles ne garantissent pas autant d’articles valides.

Exécuter la section choisie avec ses définitions. Les sections redéfinissent certains noms communs : relancer la cellule correspondante avant une reprise manuelle. Les exports sont ouverts en écriture et peuvent être remplacés; conserver une copie des résultats utiles avant de relancer.

## Importer

Lancer `docker compose up -d mongo`. Dans la dernière cellule, choisir `FILE = "lepoint_articles.jsonl"` ou `FILE = "gamespot_articles.jsonl"`. Le défaut lit GameSpot, utilise `mongodb://localhost:27017/`, la base `tx_analyzer`, la collection `articles` et des lots de 500 opérations.

L’upsert par URL peut actualiser un article existant. L’import ne passe pas par les files du collecteur intégré. Après import, suivre [les commandes d’analyse](utilisation.md#analyse-et-graphique).

Le notebook est livré sans sorties ni compteurs d’exécution. JSONL, checkpoints et profils navigateur restent locaux.

# Contribuer

Pour adapter une source, commencer par [sources.py](app/scraping/sources.py) : domaine, rubriques, motifs d’article, exclusions et sélecteurs. La découverte partagée est dans collector.py, le rendu Selenium dans browser.py et les parcours Playwright dans le notebook.

Décrire le problème, le résultat attendu et les vérifications réellement effectuées. Commencer les vérifications réseau avec un faible plafond, puis contrôler titre, texte, source et déduplication avant une collecte plus large.

Mettre les guides à jour lorsqu’un paramètre change. Retirer sorties et compteurs du notebook avant un commit. Garder JSONL, secrets, profils navigateur et archives de livraison hors du dépôt.

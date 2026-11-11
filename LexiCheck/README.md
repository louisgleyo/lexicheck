# LexiCheck v3.5 — Renvois aux lois, ordonnances et décrets numérotés

## Pourquoi cette version ?

La version précédente savait identifier « article L. 212-34 du code du patrimoine », mais **pas** « l'article 3 de la loi n° 84-53 du 26 janvier 1984 ». Elle proposait donc des concordances concernant le Code minier tout en omettant le renvoi réellement problématique de **l'article L. 4141-2 du CGCT**.

## Corrections

- Reconnaissance des renvois explicites du type `article 3 de la loi n° 84-53` (également pour les ordonnances et décrets numérotés). Les subdivisions `1° et 2°` précédant le renvoi ne sont pas prises pour des numéros d'articles.
- Recherche `POST /search` de PISTE dans le fonds `LODA_DATE` avec les champs `NUM` et `NUM_ARTICLE`. Si aucune version actuelle n'est trouvée, recherche à des dates historiques. Validation du numéro d'article et du numéro et type du texte avant de retenir un identifiant LEGIARTI.
- Affichage et coloration dans le texte de la mention « l'article 3 de la loi n° 84-53 », y compris lorsqu'il n'y a pas d'hyperlien Légifrance. Si le numéro de texte ou l'article est ambigu, la mention reste non résolue.
- Pour le cas documenté **CGCT L. 4141-2 et L. 3131-2 → loi 84-53, article 3**, suggestion de remplacement **« I de l'article L. 332-23 du code général de la fonction publique »**, explicitement attribuée au **rapport du Sénat n° 260 (2023-2024)** (https://www.senat.fr/rap/l23-260/l23-2604.html). Cette suggestion documentée n'est **pas** une concordance automatiquement découverte dans l'API. Elle est présentée « correction à vérifier », et jamais comme remplacement de rédaction identique.
- Lien correct vers le Code général de la fonction publique depuis la suggestion.

## Déploiement Render

Dans ton dépôt GitHub, remplace `server.py` et `static/app.js` (les autres fichiers ne changent pas). Fais **Commit changes**, puis laisse Render redéployer automatiquement. Pas de nouvelle dépendance, pas de nouvelle variable d'environnement, ne publie pas tes clés PISTE.

Paramètres inchangés :
- Build Command : `echo "No dependencies"`
- Start Command : `python server.py`

## Limites

Le moteur ne détecte pas encore tous les modèles de citations législatives (par exemple « article 3 de la loi précitée » ou les mentions cassées entre plusieurs éléments HTML). La recherche LODA a été vérifiée avec des réponses PISTE **simulées**, pas avec un accès réel à tes clés API. L'attribution d'une correspondance ne prouve pas l'équivalence juridique des dispositions.

## Tests

```bash
python -m unittest discover -s tests -v
```

44 tests locaux, dont 5 tests sur les renvois non codifiés et un scénario d'analyse intégrée reproduisant l'article L. 4141-2.

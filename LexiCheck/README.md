# LexiCheck v3.10 — rapprochement des renvois avec les métadonnées Légifrance

Cette version ajoute une étape **générique** de récupération des références textuelles non résolues à partir de l'indexation officielle `liensCite` (endpoint `consult/relatedLinksArticle`). Aucun renvoi, article de remplacement ni exemple parlementaire n'est codé en dur.

## Fonctionnement

Pour chaque référence réellement présente dans la rédaction :

1. L'outil cherche des candidats parmi les liens indexés de **l'article source**. Ces liens ne sont jamais considérés comme des citations rédigées par leur seule présence dans les métadonnées.
2. Pour chaque lien candidat (budget paramétrable), il consulte la fiche `consult/getArticle` de l'article cité.
3. Il compare le **numéro effectif** de l'article cité avec le numéro du renvoi, ainsi que son **code** avec le code identifié dans la proposition. L'intitulé `Code minier (nouveau)` peut être rapproché de `Code minier`, mais une homonymie avec un autre code est rejetée. Lorsqu'une fiche d'article n'a pas de `textTitles`, un titre de citation indexée ne peut servir qu'avec une nature `CODE` et un numéro indépendamment confirmé.
4. Pour les lois/ordonnances/décrets, il rapproche simultanément **type de texte**, **numéro de texte** et **numéro d'article**.
5. Si plusieurs articles consolidés (`cid`) restent compatibles, aucune cible n'est choisie. Si une seule correspondance est établie, elle est marquée « retrouvée dans les métadonnées Légifrance », puis son état est contrôlé normalement.
6. Si l'indexation ne résout pas la référence, la **recherche par numéro et code** déjà présente continue de fonctionner. Désormais, un lien seulement indexé avec le même numéro ne court-circuite plus cette recherche.
7. Les liens indexés **sans correspondance textuelle** restent dans la section repliée, en dehors des anomalies de rédaction.

La recherche demeure **partielle** si les métadonnées sont lacunaires, si le quota d'appels est atteint ou si les résultats sont ambigus. Aucun modèle d'IA ni similarité approximative ne peut à lui seul justifier l'identification d'un article cible.

## Déployer sur Render

Conserver le dépôt existant et ses variables OAuth sur Render. Remplacer :
- `server.py`
- `static/app.js`
- `static/index.html`

**Conserver à la racine `code_catalog.py`**, introduit en v3.9 (présent dans le ZIP complet). Ne jamais committer le fichier `.env` ni les secrets PISTE.

Après le commit et le déploiement, vérifier :

```
https://VOTRE-SITE.onrender.com/api/health
```

Le JSON doit contenir :

```json
{"status":"ok","version":"3.10.0","build":"indexation-raccordement-verifie-2026-11-11"}
```

Actualiser ensuite la page avec `Ctrl+F5` pour renouveler le JavaScript.

### Options facultatives

- `METADATA_MATCH_LIMIT=18` : nombre maximal de fiches citées examinées par article isolé.
- `METADATA_MATCH_BULK_LIMIT=6` : même limite par article en analyse de code/section.

Le moteur de rapprochement effectue des appels API supplémentaires, surtout quand des liens indexés n'ont pas de numéro apparent. Ajuster prudemment ces plafonds en fonction du quota PISTE.

## Tests

```bash
python -m unittest discover -s tests -q
```

Les tests simulent les réponses PISTE, notamment des références avec et sans hyperlien, les noms de codes « (nouveau) », un mauvais code avec numéro homonyme, des liens indexés ambigus, une référence à une loi non codifiée, et des liens qui ne figurent pas dans la rédaction. **Ils ne remplacent pas une validation de bout en bout sur l'API PISTE réelle.**

# LexiCheck v3.3 — Correction de l'analyse des plages d'articles

Cette version corrige notamment le cas **CGCT, art. L. 1421-3** : « articles L. 212-34 à L. 212-36 du code du patrimoine ».

## Changements

- Les plages simples sont **décomposées en autant d'articles à vérifier** (ici L. 212-34, L. 212-35 et L. 212-36), au lieu de traiter uniquement le premier identifiant trouvé dans l'indexation.
- Chaque numéro est résolu par une recherche `NUM_ARTICLE` + `NOM_CODE`, y compris dans les anciennes versions si la disposition n'est plus en vigueur. Chaque article est ensuite inspecté individuellement.
- Un hyperlien Légifrance couvrant une plage n'est plus considéré comme la preuve que tous les articles de la plage pointent vers le même `LEGIARTI`.
- Dans le texte, la **plage entière est annotée** et l'infobulle présente le statut de chaque numéro. Si certains numéros ne peuvent pas être résolus, la plage reste en violet (« non résolu »), même si un autre numéro est abrogé.
- La liste « Renvois identifiés » affiche chaque article résolu séparément. La ligne de synthèse distingue le nombre de **passages annotés** du nombre d'**articles cibles vérifiés**.
- Les suffixes `(V)` présents dans les libellés d'indexation sont masqués dans les titres : ce ne sont pas des indications suffisantes sur la vigueur actuelle de l'article cible.
- Les plages complexes, comme « L. 212-4-1 à L. 212-10 », ne sont **pas développées arbitrairement**. Les bornes sont examinées et un avertissement indique que l'énumération peut être incomplète.
- Un champ de date manquant n'affiche plus « version du » suivi de rien.

## Installation sur ton Render actuel

À partir du ZIP, dans ton dépôt GitHub, remplace :

1. `server.py`
2. `static/app.js`
3. `static/style.css`

Fais **Commit changes**. Render redéploiera le site automatiquement si Auto Deploy est activé. Il n'y a **aucune nouvelle dépendance**, aucune nouvelle variable obligatoire, et il ne faut **pas publier de clé PISTE** dans GitHub.

Les paramètres existants restent inchangés : Build `echo "No dependencies"`, Start `python server.py`, `PISTE_CLIENT_ID`, `PISTE_CLIENT_SECRET`, `PISTE_ENV=production`, `HOST=0.0.0.0`.

## Vérification

```bash
python -m unittest discover -s tests -v
```

**36 tests locaux** passent, dont un scénario simulant le lien HTML de L. 212-34 englobant la plage complète : le résultat contient alors 3 articles à contrôler.

⚠️ **Limite :** ces tests utilisent des réponses simulées de PISTE. Il faut encore vérifier le comportement exact avec les données de production. La recherche par numéro peut être ambiguë, incomplète ou limitée par les quotas, auquel cas le statut reste « non résolu ». Le fait qu'un texte ancien soit abrogé ne suffit pas à établir qu'un renvoi est juridiquement erroné.

Concernant ce cas précis, le Conseil d'État, dans son avis du 25 mai 2023 relatif à la lisibilité du droit des collectivités locales, mentionne l'article L. 123-3 du Code du patrimoine comme nouveau renvoi pertinent. Cela ne prouve pas que les trois anciens articles ont une rédaction identique à L. 123-3 : l'outil ne doit pas proposer de remplacement « identique » sans comparaison fiable.

## Fonctionnalités conservées

- Analyse d'un article (version actuelle ou historique), d'une section ou d'un code par lots.
- Recherche de renvois dans l'indexation, les hyperliens et les références rédigées en toutes lettres.
- Suggestions de concordances, avec comparaison des contenus lorsqu'ils sont disponibles.
- Export CSV, interface épurée, mentions légales, option de mot de passe `APP_ACCESS_PASSWORD`.
- Backend Python 3.10+ sans bibliothèque tierce et secrets OAuth côté serveur.

Données : Légifrance / DILA. LexiCheck est une application indépendante, sans valeur juridique officielle.

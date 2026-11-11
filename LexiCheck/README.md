# LexiCheck v3.7 — suggestions génériques, sans correctifs par article

Cette version remplace le moteur de proposition de corrections. **Aucun article, texte ou identifiant de remplacement n'est codé en dur.** Une proposition doit être justifiée par les liens de concordance effectivement retournés par l'API officielle Légifrance.

## Fonctionnement

1. LexiCheck extrait les mentions de l'article source : liens HTML, références en texte brut et plages d'articles ; pour les textes non codifiés, il tente d'identifier la loi, le décret ou l'ordonnance par son numéro.
2. Il vérifie si l'article cité est toujours en vigueur, ou s'il existe une version actuelle du **même article** (même CID). Le simple changement de version `LEGIARTI` ne justifie pas une correction de référence.
3. Pour un article réellement abrogé, remplacé ou indéterminé, il interroge **`/consult/concordanceLinksArticle`** sur sa version citée puis sur plusieurs versions historiques du même CID (lorsqu'elles sont connues).
4. Il suit, si nécessaire, une chaîne de concordances entre articles historiques jusqu'à un article en vigueur. Les chemins et articles trouvés sont dédupliqués par CID juridique et conservés comme provenance.
5. **Un seul successeur actuel** et un texte intégral rigoureusement identique (après suppression du HTML/espaces redondants) : « Correction proposée · rédaction identique ». Rédactions différentes : « Correction à vérifier ». Plusieurs successeurs, erreurs d'API ou recherche incomplète : **aucun remplacement automatique**.
6. Les renvois réellement présents dans la rédaction restent distincts des liens seulement indexés. Les références impossibles à résoudre restent violettes.

### Limites techniques et juridiques

- L'API **n'offre pas de table de concordance universelle, garantie exhaustive**. Si elle ne renvoie pas de successeur, LexiCheck indique qu'aucun successeur n'a été **confirmé** ; il n'invente pas de correction. L'article 3 de la loi n° 84-53 vers l'article L. 332-23 du CGFP *ne sera donc proposé que si les données API permettent réellement d'établir cette concordance*. La proposition précédemment injectée à la main a été supprimée.
- Recherche des concordances volontairement **bornée** pour préserver les quotas PISTE (par défaut 9 appels et 3 niveaux de chaîne par référence, 5 versions initiales). Une recherche limitée ou interrompue est signalée comme partielle et ne donne pas de correction certaine. Paramètres : `CONCORDANCE_MAX_CALLS`, `CONCORDANCE_MAX_HOPS`, `CONCORDANCE_VERSION_PROBES`.
- La recherche historique des lois non codifiées utilise désormais `LODA_ETAT` et un échantillonnage de dates **générique** (sans date de réforme privilégiée). Elle n'est pas exhaustive, notamment sur les lois très anciennes ; aucun numéro n'est associé à un texte différent sans vérification du titre du texte et de l'article.
- Une identité textuelle **n'établit pas** une équivalence de portée juridique. Les corrections proposées doivent être vérifiées avant application.

## Installation sur Render

Remplacer dans le dépôt GitHub :

- `server.py`
- `static/app.js`
- `static/index.html`

Conserver `static/style.css`, `static/mentions-legales.html`, les identifiants OAuth PISTE et les autres fichiers. Render : **Build Command** `echo "No dependencies"` et **Start Command** `python server.py` ; `HOST=0.0.0.0`.

Après déploiement, `https://votre-app.onrender.com/api/health` doit contenir `"version": "3.7.0"` ; actualiser le navigateur avec `Ctrl + F5`.

## Tests locaux

```bash
python -m unittest discover -s tests -q
```

Les tests utilisent des réponses PISTE **simulées**, sans aucune clé réelle. Ils couvrent notamment les concordances simples, les chaînes de transferts, l'historique d'un même CID, l'ambiguïté, les références non résolues et l'absence de correspondance. **Les appels effectifs à PISTE restent à valider sur l'instance déployée.**

API officielle : https://www.legifrance.gouv.fr/contenu/pied-de-page/foire-aux-questions-api

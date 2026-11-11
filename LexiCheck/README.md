# LexiCheck v3.9 — catalogue officiel et noms de codes génériques

**Changement par rapport à la v3.8 :** l'ancienne exception de recherche « Code minier » est supprimée. Les intitulés de codes proviennent maintenant d'un catalogue transversal : `code_catalog.py`, contenant les **108 intitulés** présentés dans [le répertoire Légifrance](https://www.legifrance.gouv.fr/liste/code) consulté le 11 novembre 2026 (y compris les codes historiques).

## Règles d'attribution et de recherche

1. Le programme reconnaît les noms officiels des **108 codes**, y compris les apostrophes droites/courbes et certaines variations d'accents ; un code appartenant à un autre alinéa ne doit pas contaminer le suivant.
2. Lorsque la rédaction cite un code sans qualifier « nouveau », les noms officiels compatibles sont essayés de façon **générique**. Il existe trois paires officiellement qualifiées `(nouveau)` : **Code forestier**, **Code de justice militaire**, **Code minier**. La version « nouveau » est recherchée en priorité lorsqu'elle figure dans le catalogue, puis l'intitulé non qualifié est essayé. Aucune branche n'est propre à un code.
3. Le programme compare ensuite **le numéro d'article ET l'intitulé du code** renvoyés par l'API avant de retenir un identifiant. Une simple variante de nom **n'établit jamais** une équivalence juridique entre deux codes.
4. Les mentions **`(ancien)`**, **`(édition 2001)`**, **`(édition 2006)`**, **`(1807)`**, ainsi que les **annexes du CGI** et les variantes territoriales sont maintenues distinctes. Exemple : « Code pénal (ancien) » ne doit pas être ramené au Code pénal actuel. Les différentes éditions du Code des marchés publics ne sont pas choisies par défaut.
5. Lorsque deux articles distincts restent possibles, LexiCheck **ne choisit pas arbitrairement** : le renvoi est conservé comme ambigu. Pour un code apparu après la date du catalogue, il essaie le nom transmis et sa variante `(nouveau)` sans présumer du résultat.
6. Les règles génériques de concordance et les contrôles des renvois de la v3.8 restent en place. Aucune suggestion de correction article→article issue d'un exemple sénatorial n'est codée en dur.

Le catalogue contient les **noms**, mais ni les dates d'effet consolidées ni une table de concordance entre codes. Un qualificatif éditorial n'implique pas l'identité juridique. Ce changement améliore la **résolution de la cible**, pas l'exhaustivité des concordances entre articles.

## Installation sur Render

Cette version ajoute **un nouveau fichier indispensable** `code_catalog.py`. À la **racine du dépôt GitHub** (au même endroit que `server.py`), ajouter/remplacer :

- `server.py` — moteur Python ;
- `code_catalog.py` — **nouveau catalogue des codes** ;
- `static/app.js` — numéro de version du frontend ;
- `static/index.html` — invalidation du cache navigateur.

Les autres fichiers peuvent rester inchangés. Conserver les clés OAuth PISTE **exclusivement dans Render → Environment**. Les commandes Render restent : **Build** `echo "No dependencies"`, **Start** `python server.py`.

Après le commit GitHub et le déploiement, `https://votre-site.onrender.com/api/health` doit indiquer :

```json
{"status":"ok","version":"3.9.0","build":"catalogue-officiel-generique-2026-11-11"}
```

Les autres champs (`configured`, `protected`) sont propres à l'instance. Actualiser ensuite la page avec **Ctrl+F5**.

## Tests

```bash
python -m unittest discover -s tests -q
```

Les tests vérifient la présence des 108 intitulés, les trois suffixes `(nouveau)`, le maintien des versions `(ancien)` et éditions séparées, les accents/apostrophes, la portée locale des citations, et l'extraction via les formats JSON habituels. **Les réponses PISTE sont simulées** : ce correctif ne constitue pas une validation sur les identifiants ou les quotas de ta production. En cas d'échec de résolution réel, inspecter les avertissements de LexiCheck et la réponse de PISTE sans divulguer les secrets.

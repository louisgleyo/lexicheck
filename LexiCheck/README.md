# LexiCheck v3 — Audit des renvois Légifrance

Application web légère en **Python standard (sans `requirements.txt`)**. Les requêtes OAuth vers PISTE sont effectuées par le serveur, jamais par le navigateur.

## Nouvelles fonctionnalités

- **Un article :** conserve la récupération de la version en vigueur à partir d'un ancien `LEGIARTI`; une case permet d'examiner volontairement la version historique fournie.
- **Un titre / une section :** coller l'URL Légifrance d'une section de code contenant `LEGITEXT…/LEGISCTA…`.
- **Un code entier :** coller une URL contenant `LEGITEXT…`, charger le sommaire, sélectionner éventuellement une section, puis analyser par lots de 10, 25 ou 40 articles. La suite est lancée automatiquement par défaut ; on peut suspendre après le lot courant.
- **Suggestions de correction :** recherche des concordances via `/consult/concordanceLinksArticle`, contrôle de leur vigueur et comparaison textuelle des dispositions. Une seule concordance actuelle + rédaction strictement identique (à espaces et balisage près) = « Correction proposée ». Si la rédaction diffère, « Correction à vérifier ». Plusieurs correspondances ou absence de texte = pas de correction certaine. **Une ancienne version d'un même article ne constitue pas une correction de référence.**
- **Lecture annotée :** article source avec les renvois surlignés et, le cas échéant, des extraits comparatifs.
- **Export CSV** des renvois examinés ; interface épurée, page mentions légales.

## Installer sur Render

1. Remplacer `server.py`, `static/index.html`, `static/app.js` et `static/style.css` de la v2 par les fichiers de ce ZIP. **Ajouter aussi** `static/mentions-legales.html`.
2. Faire un **Commit** dans GitHub. Render redéploiera automatiquement si « Auto Deploy » est activé.
3. Conserver les commandes : **Build** `echo "No dependencies"` et **Start** `python server.py`.
4. Conserver les variables existantes `PISTE_CLIENT_ID`, `PISTE_CLIENT_SECRET`, `PISTE_ENV=production`, `HOST=0.0.0.0` et `MAX_REFERENCES=100`. **Ne jamais mettre le secret OAuth dans GitHub.**
5. Ajouter les variables facultatives ou requises ci-dessous dans **Render → Environment** :

| Variable | Rôle |
|---|---|
| `APP_ACCESS_PASSWORD` | **Recommandé** : protège les audits et le quota PISTE par un mot de passe demandé dans la page. À laisser absent seulement si l'accès public est souhaité. |
| `JOB_BATCH_MAX` | Maximum d'articles traités par lot (40 par défaut ; plafond technique 100). |
| `BULK_REF_LIMIT` | Maximum de renvois analysés par article dans les lots (25 par défaut). Un dépassement est signalé comme **partiel**. Le mode article conserve `MAX_REFERENCES`. |
| `SITE_EDITOR_NAME` | Nom de l'éditeur / responsable de publication. |
| `SITE_EDITOR_EMAIL` | Adresse de contact. |
| `SITE_EDITOR_ADDRESS` | Adresse de l'éditeur. |
| `SITE_EDITOR_PHONE` | Téléphone de l'éditeur. |

**Mentions légales à compléter avant la mise en ligne publique** : les variables `SITE_EDITOR_*` ne sont pas renseignées par défaut. Vérifier aussi les mentions de confidentialité selon les traitements réellement mis en place. Il n'est pas nécessaire de créer un `requirements.txt`.

### Utilisation en local

`python server.py` (Python 3.10+), puis `http://127.0.0.1:8765`. Pour un usage local, copier `.env.example` en `.env` et y renseigner les identifiants. Ne jamais committer `.env`.

### Endpoints

- `GET /api/health` : état du serveur et indication de protection.
- `POST /api/analyze` : `{ "article": "LEGIARTI...", "mode": "actuel" }` ou `"historique"`.
- `POST /api/outline` : `{ "value": "https://...LEGITEXT...", "type": "code" }` ou `"section"` (dans ce cas l'URL doit inclure `LEGISCTA...`).
- `POST /api/jobs` : `{ "ids": ["LEGIARTI...", ...], "offset": 0, "count": 25 }`, retour d'un identifiant de tâche.
- `GET /api/jobs/<id>` : progression de la tâche et résultats du lot.
- `GET /mentions-legales` : mentions du site ; `GET /api/legal` fournit les informations d'éditeur configurées.

Si `APP_ACCESS_PASSWORD` est défini, les endpoints d'audit exigent `X-LexiCheck-Access` dans l'en-tête. **Ne jamais y placer le secret OAuth PISTE.**

## Couverture et limites

- Les appels officiels sont : `/consult/legi/tableMatieres`, `/consult/getArticle`, `/consult/getArticleByCid`, `/consult/relatedLinksArticle` et `/consult/concordanceLinksArticle`.
- Le sommaire doit être fourni par l'API et peut être incomplet selon les textes et la date. Si aucun article n'est trouvé, l'application refuse de présenter un audit vide comme exhaustif.
- Les liens structurés et liens HTML peuvent manquer certains renvois implicites ou non hyperliés. Les métadonnées de citation ne prouvent pas qu'une référence figure littéralement dans le corps du texte.
- Les comparaisons portent sur **le contenu intégral** des articles (suppression des balises, normalisation Unicode et blancs), et non sur leur seul intitulé. La similarité en pourcentage porte au maximum sur les 5 000 premiers caractères et ne vaut pas preuve d'équivalence juridique.
- Un article abrogé cité aujourd'hui n'est **pas automatiquement une erreur** (situations transitoires, historiques, etc.). Les propositions ne modifient aucun texte officiel et nécessitent une validation humaine.
- **Render gratuit :** les jobs tournent dans la mémoire du serveur et peuvent disparaître si l'instance s'endort ou redémarre ; l'interface doit rester ouverte pour enchaîner les lots. Une analyse d'un très grand code peut prendre longtemps et atteindre les quotas PISTE. Un déploiement persistant avec file de tâches est recommandé pour un audit national exhaustif. Le bouton « Suspendre » empêche d'enchaîner les nouveaux lots mais laisse terminer le lot engagé.
- Par défaut, les lots de 40 articles maximum limitent les appels simultanés à PISTE. Un lot peut prendre plusieurs minutes. Des articles manquants sont listés comme erreurs : **ne pas interpréter ces absences comme des articles sans problème**.

## Tests

`python -m unittest discover -s tests -v`

24 tests unitaires passent avec réponses API simulées. Le frontend a été testé en navigateur avec réponses simulées sur ordinateur et mobile. **Les nouveaux parcours en masse restent à vérifier avec des identifiants PISTE réels.**

## Crédits et conditions

Textes et métadonnées : Légifrance / DILA, Licence ouverte 2.0, sous réserve des CGU de PISTE et de l'API Légifrance. LexiCheck est indépendant de la DILA.

# LexiCheck — audit des renvois d'un article Légifrance

Application web **une page** et serveur Python local. Elle appelle **réellement l'API officielle Légifrance via PISTE** pour un identifiant `LEGIARTI` donné.

## Ce qu'elle fait

- Collez l'URL d'un article Légifrance ou son identifiant `LEGIARTI...`.
- Charge l'article avec `POST /consult/getArticle`.
- Regroupe les citations sortantes indexées par `POST /consult/relatedLinksArticle` (`liensCite`, **pas** `liensCitePar`) et les liens `LEGIARTI`/`JORFARTI` détectés dans son `texteHtml`.
- Vérifie l'état juridique et les dates des références `LEGIARTI` via `POST /consult/getArticle`.
- Lorsque nécessaire, tente `POST /consult/getArticleByCid` pour distinguer une ancienne version d'un article toujours en vigueur.
- Tente `POST /consult/concordanceLinksArticle` pour identifier de possibles correspondances lors de remplacements ou renumérotations.
- Affiche un tableau filtrable, des indicateurs, les avertissements, les mentions textuelles non résolues, et un export CSV.

## Démarrage local — aucune bibliothèque à installer

**Prérequis :** Python 3.10 ou version plus récente.

1. Créez un compte sur <https://piste.gouv.fr/>, acceptez les CGU de l'API Légifrance et ajoutez l'API Légifrance à votre application PISTE (production ou sandbox).
2. Dupliquez `.env.example` en `.env` dans ce dossier.
3. Remplissez les deux champs `PISTE_CLIENT_ID` et `PISTE_CLIENT_SECRET`. Si vos clés sont de sandbox, choisissez `PISTE_ENV=sandbox`.
4. Démarrez l'application : `python server.py` dans ce dossier (sur Windows, vous pouvez aussi double-cliquer `lancer_windows.bat`).
5. Ouvrez <http://127.0.0.1:8765> dans votre navigateur.

**Exemple de `.env` :**

```dotenv
PISTE_ENV=production
PISTE_CLIENT_ID=votre_client_id
PISTE_CLIENT_SECRET=votre_client_secret
MAX_REFERENCES=100
```

Le fichier `.env` est volontairement exclu du dépôt Git. Les clés ne sont **jamais envoyées au navigateur** : seul le serveur Python les lit.

## Fonctionnement et états

| État affiché | Signification |
|---|---|
| En vigueur | La version citée est indiquée comme en vigueur à la date du contrôle. |
| Ancienne version | L'identifiant cité n'est plus actuel, mais une version en vigueur du **même CID** a été identifiée. |
| Alerte potentielle | La référence est indiquée abrogée/remplacée et aucune version courante n'a été confirmée. Ce n'est **pas** une preuve d'erreur. |
| Indéterminé | L'information retournée par l'API ne permet pas de conclure (article JORF, échec d'appel, absence de dates, etc.). |

La concordance avec un autre identifiant est affichée en **hypothèse**, jamais en correction automatique.

### Limites importantes

- Les liens indexés par Légifrance ne prouvent pas qu'une référence se trouve dans le *corps de la rédaction* ; les liens présents dans le HTML sont identifiés séparément.
- Les mentions textuelles sans hyperlien et les renvois implicites ne sont pas tous identifiables automatiquement ; quelques formes `article L. 123-1` sont listées à titre indicatif, sans être considérées comme vérifiées.
- Les résultats de `getArticleByCid` peuvent être incomplets ou de structure différente selon les fonds : dans ce cas l'application préfère « indéterminé » à une conclusion erronée.
- Un article abrogé peut être cité volontairement pour une situation passée. Une renumérotation ne signifie pas systématiquement que le renvoi est juridiquement erroné.
- Les URL de consultation Légifrance reconstituées (`codes/article_lc` et `loda/article_lc`) peuvent nécessiter une correction manuelle suivant le fonds.
- Les quotas PISTE s'appliquent : nombre maximal de cibles analysées par requête `MAX_REFERENCES` (100 par défaut), 4 appels simultanés au plus. Les références au-delà de cette limite sont signalées mais non contrôlées.
- **Nécessite des clés PISTE personnelles :** le fonctionnement externe ne peut pas être testé sans elles. Les tests inclus couvrent l'analyse sur données simulées.

## Tester les règles sans identifiants

```bash
python -m unittest discover -s tests -v
```

## Sources officielles

- [DILA – FAQ API Légifrance](https://www.legifrance.gouv.fr/contenu/pied-de-page/foire-aux-questions-api)
- [API Légifrance sur data.gouv.fr](https://www.data.gouv.fr/dataservices/legifrance)
- [Portail PISTE](https://piste.gouv.fr/)

## Structure

```text
server.py          # Serveur web local + client OAuth 2.0 PISTE + logique de vérification
static/index.html  # Interface une page
static/style.css   # Style responsive
static/app.js      # Recherche, filtres, export CSV, rendu des résultats
.env.example       # Configuration à dupliquer (ne jamais publier vos clés)
tests/             # Tests unitaires sans accès à PISTE
lancer_windows.bat # Démarrage Windows
```

# LexiCheck v3.6 — cohérence texte / résultats

Cette version corrige deux problèmes identifiés sur l'article L. 4141-2 du CGCT :

1. Les références vers une **loi non codifiée**, comme « l'article 3 de la loi n° 84-53 », peuvent être réparties entre plusieurs balises HTML (`span`, `strong`, liens). Elles sont désormais reconnues et annotées dans le flux textuel entier, même si l'API ne parvient pas à retrouver leur article cible. Dans ce dernier cas, la référence reste **violette**, et une correction explicitement documentée par le Sénat est signalée comme telle, **sans prétendre être une concordance API**.
2. Un lien présent dans les **métadonnées d'indexation** ne prouve pas qu'il est cité dans la rédaction. Les liens non retrouvés dans le texte sont désormais affichés séparément, dans un panneau replié, et **ne sont plus comptés dans les statistiques de renvois du texte**, y compris en analyse par lots.

### Article 3 de la loi de 1984

La recherche classique dans LODA est conservée. Si elle échoue pour l'article 3 de la loi n° 84-53, le serveur tente une consultation directe de l'identifiant historique connu `LEGIARTI000038922660`. Il ne le retient **que si l'API confirme le numéro de l'article et le titre de la loi**. En cas d'échec, l'article reste « non résolu » et seule la suggestion documentaire du Sénat est affichée : remplacement par « I de l'article L. 332-23 du Code général de la fonction publique ». Source : https://www.senat.fr/rap/l23-260/l23-2604.html

### Autres améliorations

- Les plages comme `L. 212-34 à L. 212-36` conservent leur vérification article par article, même si un hyperlien couvre toute la plage.
- Les références figurant dans plusieurs éléments HTML inline sont repérées sans supprimer les paragraphes ni la mise en forme du texte.
- La version `3.6.0` apparaît dans l'en-tête et dans `/api/health`, ainsi que dans le résultat de l'analyse, afin de diagnostiquer un déploiement non actualisé sur Render.
- Les fichiers CSS et JS portent un numéro de version dans leur URL, afin d'éviter un décalage entre l'ancien frontend et le nouveau serveur.

### Déploiement Render

Dans le dépôt GitHub déjà connecté à Render, remplacer **les quatre fichiers** :

- `server.py`
- `static/app.js`
- `static/index.html`
- `static/style.css`

Conserver les autres fichiers (dont les mentions légales) et les variables d'environnement déjà configurées. Commit puis attendre la fin du déploiement. Vérifier le champ `version` à `https://VOTRE-SITE.onrender.com/api/health` : il doit valoir `3.6.0`. Actualiser ensuite la page et vérifier que l'en-tête indique `v3.6.0`.

Configuration Render inchangée :

- Build : `echo "No dependencies"`
- Start : `python server.py`

### Tests

```bash
python -m unittest discover -s tests -q
```

49 tests locaux réussis au moment de l'export, dont 5 nouveaux tests reproduisant : extraction à travers plusieurs balises HTML, résolution par article historique après échec de `/search`, conservation d'un statut indéterminé si l'API ne confirme pas l'article, séparation des liens indexés, et assainissement du HTML.

### Limite importante

Les appels PISTE n'ont **pas** été testés ici avec des identifiants réels. L'API peut renvoyer des champs différents des réponses simulées. Une correspondance documentaire ou lexicale ne constitue jamais une validation juridique de la substitution proposée.

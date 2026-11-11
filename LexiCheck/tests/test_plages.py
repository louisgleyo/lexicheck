"""Cas réel reproduit : L1421-3 du CGCT, plage L212-34 à L212-36."""
import datetime as dt
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

DAY = dt.date(2026, 11, 11)
SRC = 'LEGIARTI000006389283'
A = 'LEGIARTI000000030034'
B = 'LEGIARTI000000030035'
C = 'LEGIARTI000000030036'
TEXT = 'Les règles relatives au droit de préemption sur les archives privées sont fixées par les dispositions des articles L. 212-34 à L. 212-36 du code du patrimoine.'


def art(ident, num, state, body):
    return {'id': ident, 'cid': ident, 'num': num, 'etat': state,
            'dateDebut': '2004-02-24', 'dateFin': '2018-01-01' if state == 'ABROGE' else '2999-01-01',
            'texteHtml': body, 'textTitles': [{'titre': 'Code du patrimoine'}]}

class Client:
    search_article_number = server.PisteClient.search_article_number

    def __init__(self, anchor=True):
        self._cache_lock = threading.Lock()
        self._lookup_cache = {}
        self.calls = []
        fragment = ('<a href="/codes/article_lc/'+A+'">articles L. 212-34 à L. 212-36</a>'
                    if anchor else 'articles L. 212-34 à L. 212-36')
        self.db = {
            SRC: art(SRC, 'L1421-3', 'VIGUEUR', '<p>Les règles relatives aux archives sont fixées par les dispositions des '+fragment+' du code du patrimoine.</p>'),
            A: art(A, 'L212-34', 'ABROGE', '<p>Texte ancien A.</p>'),
            B: art(B, 'L212-35', 'ABROGE', '<p>Texte ancien B.</p>'),
            C: art(C, 'L212-36', 'ABROGE', '<p>Texte ancien C.</p>'),
        }
        self.db[SRC]['textTitles']=[{'titre':'Code général des collectivités territoriales'}]

    def article(self, ident):
        return self.db[ident]

    def call(self, endpoint, request):
        self.calls.append((endpoint, request))
        if endpoint == 'consult/relatedLinksArticle':
            return {'liensCite': [{'id': A, 'name': 'Code du patrimoine. - art. L212-34 (V)', 'nature': 'CODE'}]}
        if endpoint == 'consult/getArticleByCid':
            return {'listArticle': []}
        if endpoint == 'consult/concordanceLinksArticle':
            return {'newTexts': []}
        if endpoint == 'search':
            q = request['recherche']['champs'][0]['criteres'][0]['valeur']
            if request['recherche']['filtres'][0]['valeurs'][0] != 'Code du patrimoine':
                raise AssertionError('Wrong code')
            if request['fond'] == 'CODE_DATE':
                return {'results': []}
            if request['fond'] == 'CODE_ETAT':
                return {'results': [{'sections': [{'extracts': [{'id': {'L212-35': B, 'L212-36': C}[q]}]}]}]}
        raise AssertionError((endpoint, request))

class RangeTests(unittest.TestCase):
    def test_expand_simple_range(self):
        self.assertEqual(server.expand_article_range('L. 212-34', 'L. 212-36'), ['L212-34', 'L212-35', 'L212-36'])
        self.assertEqual(server.expand_article_range('R. 100-1', 'R. 100-3'), ['R100-1', 'R100-2', 'R100-3'])

    def test_complex_and_large_ranges_are_not_guessed(self):
        self.assertIsNone(server.expand_article_range('L. 212-4-1', 'L. 212-10'))
        self.assertIsNone(server.expand_article_range('L. 1', 'L. 9999'))
        groups,warnings=server.textual_mentions('Les articles L. 212-4-1 à L. 212-10 du code du patrimoine.')
        self.assertEqual(groups[0]['numbers'],['L212-4-1','L212-10'])
        self.assertEqual(len(warnings),1)

    def test_expanded_results_from_plain_text(self):
        client=Client(anchor=False)
        out=server.analyze(client,SRC,DAY)
        self.assertEqual(len(out['renvois']),3)
        self.assertEqual(out['comptes']['alerte'],3)
        self.assertEqual(out['mentionsNonResolues'],[])
        self.assertIn('legal-range status-alerte',out['sourceHtmlAnnote'])
        self.assertEqual(out['renvoisSurlignes'],1)
        self.assertEqual({e['numero'] for e in out['renvois']},{'L212-34','L212-35','L212-36'})
        searches=[params['recherche']['champs'][0]['criteres'][0]['valeur'] for route,params in client.calls if route=='search']
        self.assertEqual(set(searches),{'L212-35','L212-36'})

    def test_anchor_on_first_article_does_not_color_entire_range_as_one(self):
        client=Client(anchor=True)
        out=server.analyze(client,SRC,DAY)
        self.assertEqual(out['comptes']['alerte'],3)
        self.assertEqual(out['renvoisSurlignes'],1)
        self.assertIn('Plage de 3 articles',out['sourceHtmlAnnote'])
        self.assertNotIn('data-ref="'+A+'"',out['sourceHtmlAnnote'])
        self.assertNotIn('(V)',out['renvois'][0]['titre'])
        self.assertEqual(out['mentionsNonResolues'],[])

    def test_partial_range_never_colored_as_all_checked(self):
        source={'texteHtml':'<p>articles L. 212-34 à L. 212-36 du code du patrimoine</p>'}
        checked=[{'id': A, 'titre':'Code du patrimoine - art. L212-34', 'statut':'alerte', 'nature':'CODE'}]
        rendered,n,ids=server.render_annotated_html(source,checked)
        self.assertIn('legal-range status-nonresolu',rendered)
        self.assertIn('L. 212-35 : non résolu',rendered)
        self.assertEqual(n,1)
        self.assertEqual(ids,{A})

if __name__=='__main__': unittest.main()

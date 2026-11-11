import datetime as dt
from pathlib import Path
import sys
import threading
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

SOURCE = 'LEGIARTI000045213963'
CURRENT = 'LEGIARTI000000000111'
ABROGED = 'LEGIARTI000000000222'
OLD_DATE = dt.date(2026, 11, 11)
BODY = ('Les règles générales sont fixées par les dispositions de '
        "l'article L. 212-4 et des articles L. 212-33 du code du patrimoine.")

def article(id, num, etat, start, end, texte='', cid=None):
    return {'id':id, 'cid':cid or id, 'num':num, 'etat':etat,
            'dateDebut':start, 'dateFin':end, 'texteHtml':'<p>'+texte+'</p>',
            'textTitles':[{'titre':'Code du patrimoine'}]}

class FakeClient:
    search_article_number=server.PisteClient.search_article_number
    def __init__(self):
        self._cache_lock=threading.Lock(); self._lookup_cache={}
        self.calls=[]
        self.db={
            SOURCE: article(SOURCE,'L1421-1','VIGUEUR','2022-01-01','2999-01-01',BODY),
            CURRENT: article(CURRENT,'L212-4','VIGUEUR','2020-01-01','2999-01-01'),
            ABROGED: article(ABROGED,'L212-33','ABROGE','2005-01-01','2018-01-01'),
        }
        self.db[SOURCE]['textTitles']=[{'titre':'Code général des collectivités territoriales'}]
    def article(self, ident):
        return self.db[ident]
    def versions(self, cid):
        return []
    def call(self, endpoint, data):
        self.calls.append((endpoint,data))
        if endpoint=='consult/relatedLinksArticle':return {'liensCite':[]}
        if endpoint=='consult/concordanceLinksArticle':return {'newTexts':[]}
        if endpoint=='search':
            value=data['recherche']['champs'][0]['criteres'][0]['valeur']
            code=data['recherche']['filtres'][0]['valeurs'][0]
            if code != 'Code du patrimoine':
                return {'results': []}
            if value=='L212-4':return {'results':[{'sections':[{'extracts':[{'id':CURRENT}]}]}]}
            if value=='L212-33' and data['fond']=='CODE_DATE' and len(data['recherche']['filtres'])==2:
                # Le 11 novembre 2026, la disposition est abrogée : aucun résultat.
                return {'results':[]}
            if value=='L212-33' and data['fond']=='CODE_ETAT':
                return {'results':[{'sections':[{'extracts':[{'id':ABROGED}]}]}]}
        raise AssertionError((endpoint,data))

class LookupTests(unittest.TestCase):
    def test_search_body_and_validate_number_code(self):
        c=FakeClient()
        out=c.search_article_number('L. 212-33','Code du patrimoine',OLD_DATE)
        self.assertEqual([a['id'] for a in out],[ABROGED])
        calls=[x for x in c.calls if x[0]=='search']
        self.assertEqual([q['fond'] for _,q in calls],['CODE_DATE','CODE_ETAT'])
        self.assertEqual(calls[0][1]['recherche']['champs'][0]['typeChamp'],'NUM_ARTICLE')
        self.assertEqual(calls[0][1]['recherche']['filtres'][0]['facette'],'NOM_CODE')
        # Cache : pas de deuxième série d'appels.
        c.search_article_number('L212-33','Code du patrimoine',OLD_DATE)
        self.assertEqual(len([x for x in c.calls if x[0]=='search']),2)

    def test_full_analysis_resolves_missing_hyperlinks(self):
        c=FakeClient()
        result=server.analyze(c,SOURCE,OLD_DATE)
        self.assertEqual(result['comptes']['alerte'],1)
        self.assertEqual(result['comptes']['vigoureux'],1)
        self.assertEqual(result['mentionsNonResolues'],[])
        self.assertIn('>L. 212-33</a>',result['sourceHtmlAnnote'])
        self.assertIn('>L. 212-4</a>',result['sourceHtmlAnnote'])
        self.assertIn('status-alerte',result['sourceHtmlAnnote'])
        self.assertIn('status-vigoureux',result['sourceHtmlAnnote'])

    def test_unqualified_reference_uses_source_code_but_not_foreign_code(self):
        c=FakeClient()
        c.db[SOURCE]['texteHtml']='<p>Se reporter à l’article L. 212-4.</p>'
        res=server.analyze(c,SOURCE,OLD_DATE)
        self.assertEqual(res['mentionsNonResolues'],['article L. 212-4'])
        searches=[b for ep,b in c.calls if ep=='search']
        self.assertTrue(searches)
        self.assertTrue(all(b['recherche']['filtres'][0]['valeurs'][0]=='Code général des collectivités territoriales' for b in searches))

    def test_ambiguous_results_remain_unresolved(self):
        c=FakeClient()
        c.db[ABROGED]['cid']='LEGIARTI000000007777'
        another='LEGIARTI000000000333'
        c.db[another]=article(another,'L212-33','ABROGE','2006-01-01','2007-01-01')
        old_call=c.call
        def altered(endpoint,data):
            if endpoint=='search' and data['recherche']['champs'][0]['criteres'][0]['valeur']=='L212-33' and data['fond']=='CODE_ETAT':
                return {'results':[{'sections':[{'extracts':[{'id':ABROGED},{'id':another}]}]}]}
            return old_call(endpoint,data)
        c.call=altered
        result=server.analyze(c,SOURCE,OLD_DATE)
        self.assertTrue(any('L. 212-33' in x for x in result['mentionsNonResolues']))
        self.assertIn('status-nonresolu',result['sourceHtmlAnnote'])

if __name__=='__main__':unittest.main()

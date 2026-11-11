"""Régressions du vrai exemple L4141-2, sans données PISTE inventées.

Les résultats de PISTE sont SIMULÉS; les LEGIARTI et l'état des trois
articles ont été vérifiés séparément sur les pages publiques Légifrance.
"""
import datetime as dt
from pathlib import Path
import sys, threading, unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

DAY = dt.date(2026,11,11)
SOURCE = 'LEGIARTI000044190643'
MINING_16 = 'LEGIARTI000046554767'
MINING_17 = 'LEGIARTI000045580612'
CGCT_4433 = 'LEGIARTI000052092395'
INDEX_ONLY = 'LEGIARTI000000000888'
HTML = '''<p>8° Les décisions ... en application des articles <span>L. 611-16</span> et <b>L. 611-17</b> du code minier ;</p>
<p>9° Les décisions ... en application de l'article L. 4433-15-1.</p>
<p>4° Les décisions relatives au recrutement en application des 1° et 2° de l'article 3 de la loi n° 84-53 du 26 janvier 1984.</p>'''


def art(i,n,t,st='2024-01-01'):
    return {'id':i,'cid':i,'num':n,'etat':'VIGUEUR',
            'dateDebut':st,'dateFin':'2999-01-01',
            'texteHtml':'<p>Article source</p>', 'textTitles':[{'titre':t}]}

class FixtureClient(server.PisteClient):
    def __init__(self):
        self._cache_lock=threading.Lock();self._lookup_cache={};self._article_cache={};self._lock=threading.Lock()
        self.calls=[]
        self.db={
            SOURCE:art(SOURCE,'L4141-2','Code général des collectivités territoriales'),
            MINING_16:art(MINING_16,'L611-16','Code minier (nouveau)','2024-07-01'),
            MINING_17:art(MINING_17,'L611-17','Code minier (nouveau)','2022-04-15'),
            CGCT_4433:art(CGCT_4433,'L4433-15-1','Code général des collectivités territoriales','2026-01-01'),
            INDEX_ONLY:art(INDEX_ONLY,'L611-32','Code minier (nouveau)')
        }
        self.db[SOURCE]['texteHtml']=HTML

    def article(self,ident):return self.db[ident]
    def versions(self,cid):return []
    def search_loda_article_number(self,*args):return []
    def call(self,endpoint,body):
        self.calls.append((endpoint,body))
        if endpoint=='consult/relatedLinksArticle':
            return {'liensCite':[{'id':INDEX_ONLY,'name':'Code minier - art. L611-32','nature':'CODE'}]}
        if endpoint=='consult/concordanceLinksArticle':return {'newTexts':[]}
        if endpoint=='search':
            filters=body['recherche']['filtres'];num=body['recherche']['champs'][0]['criteres'][0]['valeur'];code=filters[0]['valeurs'][0]
            key=(num,code)
            ids={('L611-16','Code minier (nouveau)'):MINING_16,
                 ('L611-17','Code minier (nouveau)'):MINING_17,
                 ('L4433-15-1','Code général des collectivités territoriales'):CGCT_4433}
            ident=ids.get(key)
            return {'results':[{'sections':[{'extracts':[{'id':ident}]}]}]} if ident else {'results':[]}
        raise AssertionError((endpoint,body))

class MiningRegression(unittest.TestCase):
    def test_code_alias_and_scope_barrier(self):
        source_article=art(SOURCE,'L4141-2','Code général des collectivités territoriales')
        flat=server.cohesive_text(HTML)
        positions={n:flat.index(n)+len(n) for n in ['L. 611-16','L. 611-17','L. 4433-15-1']}
        self.assertEqual(server.code_for_mention(flat,positions['L. 611-16'],source_article),'Code minier')
        self.assertEqual(server.code_for_mention(flat,positions['L. 611-17'],source_article),'Code minier')
        self.assertEqual(server.code_for_mention(flat,positions['L. 4433-15-1'],source_article),'Code général des collectivités territoriales')
        self.assertTrue(server.same_code_title('Code minier','Code minier (nouveau)'))
        self.assertFalse(server.same_code_title('Code minier','Code général des collectivités territoriales'))

    def test_piste_search_filters_verified_against_official_name(self):
        c=FixtureClient()
        out=c.search_article_number('L611-16','Code minier',DAY)
        self.assertEqual([x['id'] for x in out],[MINING_16])
        calls=[b for op,b in c.calls if op=='search']
        self.assertEqual(calls[0]['recherche']['filtres'][0],{'facette':'NOM_CODE','valeurs':['Code minier (nouveau)']})
        self.assertEqual(len(calls),1)

    def test_full_example_no_manual_senate_and_three_current_articles(self):
        c=FixtureClient()
        d=server.analyze(c,SOURCE,DAY)
        self.assertEqual(d['versionMoteur'],'3.9.0')
        self.assertEqual({x['id'] for x in d['renvois']},{MINING_16,MINING_17,CGCT_4433})
        self.assertEqual(d['comptes']['vigoureux'],3)
        self.assertEqual([x['id'] for x in d['liensIndexationSeule']],[INDEX_ONLY])
        self.assertNotIn('Sénat',str(d))
        self.assertNotIn('332-23',str(d))
        self.assertNotIn('L611-16',' '.join(d['mentionsNonResolues']))
        self.assertNotIn('L611-17',' '.join(d['mentionsNonResolues']))
        self.assertNotIn('L4433-15-1',' '.join(d['mentionsNonResolues']))
        self.assertTrue(any('84-53' in u for u in d['mentionsNonResolues']))
        self.assertIn('status-vigoureux',d['sourceHtmlAnnote'])

    def test_wrong_code_title_never_accepted(self):
        c=FixtureClient()
        c.db[MINING_16]['textTitles']=[{'titre':'Code du patrimoine'}]
        result=c.search_article_number('L611-16','Code minier',DAY)
        self.assertEqual(result,[])

    def test_no_hardcoded_senate_rule(self):
        source=Path(server.__file__).read_text(encoding='utf-8')
        for term in ('LEGIARTI000044426650', 'rapport sénatorial', 'remplacement préconisé par le Sénat'):
            self.assertNotIn(term,source)

if __name__ == '__main__':unittest.main()

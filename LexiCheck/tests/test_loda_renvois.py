import datetime as dt
from pathlib import Path
import sys, threading, unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

SOURCE = 'LEGIARTI000044190643'
OLD = 'LEGIARTI000038922660'  # Loi 84-53, article 3
NEW = 'LEGIARTI000044426650'  # CGFP L332-23
DAY = dt.date(2026, 11, 11)
TEXT = "I.- Sont transmis : 4° décisions des agents, à l'exception de celles prises en application des 1° et 2° de l'article 3 de la loi n° 84-53 du 26 janvier 1984 portant dispositions statutaires relatives à la fonction publique territoriale."

class FakeClient:
    search_loda_article_number = server.PisteClient.search_loda_article_number
    def __init__(self):
        self._cache_lock = threading.Lock()
        self._lookup_cache = {}
        self.calls = []
        self.db = {
            SOURCE: {'id': SOURCE,'cid':SOURCE,'num':'L4141-2','etat':'VIGUEUR','dateDebut':'2022-07-01', 'dateFin':'2999-01-01','texteHtml':'<p>'+TEXT+'</p>','textTitles':[{'titre':'Code général des collectivités territoriales'}]},
            OLD: {'id':OLD,'cid':OLD,'num':'3','etat':'ABROGE','dateDebut':'2019-08-08','dateFin':'2022-03-01','texteHtml':'<p>I. Les collectivités recrutent des agents temporaires en cas de besoin saisonnier. II. Autre disposition.</p>', 'textTitles':[{'titre':'Loi n° 84-53 du 26 janvier 1984 portant dispositions statutaires relatives à la fonction publique territoriale'}]},
            NEW: {'id':NEW,'cid':NEW,'num':'L332-23','etat':'VIGUEUR','dateDebut':'2022-03-01','dateFin':'2999-01-01','texteHtml':'<p>Les collectivités recrutent en cas de besoin saisonnier.</p>','textTitles':[{'titre':'Code général de la fonction publique'}]},
        }
    def article(self, ident):
        return self.db[ident]
    def versions(self, cid):
        return []
    def call(self, endpoint, data):
        self.calls.append((endpoint,data))
        if endpoint=='consult/relatedLinksArticle':return {'liensCite':[]}
        if endpoint=='consult/concordanceLinksArticle':return {'newTexts':[]}
        if endpoint=='search':
            q=data['recherche'];assert [x['typeChamp'] for x in q['champs']]==['NUM','NUM_ARTICLE']
            assert [x['criteres'][0]['valeur'] for x in q['champs']]==['84-53','3']
            if data['fond']=='LODA_ETAT':return {'results':[]}
            assert data['fond']=='LODA_DATE'
            if not q['filtres']:return {'results':[]}
            date_ms=q['filtres'][0]['singleDate'];day=dt.datetime.fromtimestamp(date_ms/1000, tz=dt.timezone.utc).date()
            return {'results':[{'sections':[{'extracts':[{'id':OLD}]}]}]} if day < dt.date(2022,3,1) else {'results':[]}
        raise AssertionError(endpoint)

class LawReferenceTests(unittest.TestCase):
    def test_literal_recognition_preserves_subsections(self):
        m=server.normative_mentions(TEXT)
        self.assertEqual(len(m),1)
        self.assertEqual((m[0]['numero'],m[0]['texteNumero'],m[0]['typeTexte']),('3','84-53','loi'))
        self.assertNotIn('1°', m[0]['raw'])
    def test_loda_search_historical_and_verified(self):
        c=FakeClient(); result=c.search_loda_article_number('3','84-53',DAY)
        self.assertEqual([x['id'] for x in result],[OLD])
        self.assertTrue(any(ep=='search' for ep,_ in c.calls))
        # A second request is a cached response
        count=len(c.calls)
        c.search_loda_article_number('3','84-53',DAY)
        self.assertEqual(len(c.calls),count)
    def test_integrated_detection_without_curated_fix(self):
        c=FakeClient();r=server.analyze(c,SOURCE,DAY)
        matching=[x for x in r['renvois'] if x.get('texteNumero')=='84-53']
        self.assertEqual(len(matching),1)
        x=matching[0]
        self.assertEqual(x['statut'],'alerte')
        self.assertEqual(x['nature'],'LODA')
        self.assertEqual(x['suggestion']['statut'],'aucune')
        self.assertNotIn('id',x['suggestion'])
        self.assertIn('status-alerte',r['sourceHtmlAnnote'])
        self.assertIn("l&#x27;article 3 de la loi n° 84-53", r['sourceHtmlAnnote'])
        self.assertEqual(r['mentionsNonResolues'],[])
    def test_ambiguous_wrong_title_not_accepted(self):
        c=FakeClient();c.db[OLD]['textTitles']=[{'titre':'Loi n° 88-53 du 26 janvier 1988'}]
        r=server.analyze(c,SOURCE,DAY)
        self.assertFalse(any(x.get('texteNumero')=='84-53' for x in r['renvois']))
        self.assertTrue(any('84-53' in x for x in r['mentionsNonResolues']))
        self.assertIn('status-nonresolu',r['sourceHtmlAnnote'])
    def test_same_article_no_wrong_curated_suggestion(self):
        c=FakeClient();c.db[SOURCE]['num']='L7777-1'
        r=server.analyze(c,SOURCE,DAY)
        x=next(x for x in r['renvois'] if x.get('texteNumero')=='84-53')
        self.assertEqual(x['statut'],'alerte')
        self.assertNotEqual(x['suggestion']['statut'],'verification')

if __name__=='__main__':unittest.main()

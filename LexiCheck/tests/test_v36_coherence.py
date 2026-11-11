"""Tests reproduisant L4141-2 et les liens indexés absents du texte."""
import datetime as dt, sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
DAY=dt.date(2026,11,11)
SRC='LEGIARTI000044190643'
OLD='LEGIARTI000038922660'
NEW='LEGIARTI000044426650'
MINING='LEGIARTI000000007777'
RAW='<p>Décisions prises en application des 1° et 2° de <span>l\'article</span> <strong>3</strong> de la <span>loi n°</span> 84-53 du 26 janvier 1984.</p>'
class Fake:
    def __init__(self, old_available=True):
        self.old_available=old_available
        self.db={
          SRC:{'id':SRC,'cid':SRC,'num':'L4141-2','etat':'VIGUEUR','dateDebut':'2022-03-01','dateFin':'2999-01-01','texteHtml':RAW,'textTitles':[{'titre':'Code général des collectivités territoriales'}]},
          OLD:{'id':OLD,'cid':OLD,'num':'3','etat':'ABROGE','dateDebut':'2019-08-08','dateFin':'2022-03-01','texteHtml':'<p>Agents temporaires.</p>','textTitles':[{'titre':'Loi n° 84-53 du 26 janvier 1984'}]},
          NEW:{'id':NEW,'cid':NEW,'num':'L332-23','etat':'VIGUEUR','dateDebut':'2022-03-01','dateFin':'2999-01-01','texteHtml':'<p>Agents.</p>'},
          MINING:{'id':MINING,'cid':MINING,'num':'L611-31','etat':'ABROGE','dateDebut':'2010-01-01','dateFin':'2015-01-01','texteHtml':'<p>Mine.</p>'}}
    def article(self,ident):
        if ident==OLD and not self.old_available:raise server.APIError('non disponible')
        return self.db[ident]
    def versions(self,cid):return []
    def search_loda_article_number(self,*args):return []
    def call(self,ep,body):
        if ep=='consult/relatedLinksArticle':return {'liensCite':[{'id':MINING,'name':'Code minier - art. L611-31','nature':'CODE'}]}
        if ep=='consult/concordanceLinksArticle':return {'newTexts':[]}
        raise AssertionError(ep)
class Regression(unittest.TestCase):
    def test_cross_inline_html(self):
        norm=server.normative_mentions(server.cohesive_text(RAW))
        self.assertEqual([(x['numero'],x['texteNumero']) for x in norm],[('3','84-53')])
    def test_detect_law_even_if_search_empty(self):
        out=server.analyze(Fake(),SRC,DAY)
        self.assertEqual(out['versionMoteur'],'3.7.0')
        # Plus aucune récupération avec un ID historique codé en dur.
        self.assertEqual(out['renvois'],[])
        self.assertEqual(out['comptes']['alerte'],0)
        self.assertTrue(any('84-53' in x for x in out['mentionsNonResolues']))
        self.assertEqual([x['id'] for x in out['liensIndexationSeule']],[MINING])
        self.assertIn('status-nonresolu',out['sourceHtmlAnnote'])
        self.assertNotIn(MINING,out['sourceHtmlAnnote'])
    def test_no_api_return_stays_unknown_with_documented_fix(self):
        out=server.analyze(Fake(False),SRC,DAY)
        self.assertEqual(out['renvois'],[])
        self.assertTrue(any('84-53' in x for x in out['mentionsNonResolues']))
        self.assertIn('status-nonresolu',out['sourceHtmlAnnote'])
        self.assertEqual(out['correctionsDocumentees'],[])
        self.assertEqual(out['comptes']['alerte'],0)
    def test_index_without_body_is_not_colored(self):
        markup,n,refs=server.render_annotated_html({'texteHtml':'<p>Pas de référence.</p>'},[{'id':MINING,'titre':'Code minier - art. L611-31','statut':'alerte'}])
        self.assertEqual(refs,set())
        self.assertNotIn('status-alerte',markup)
    def test_untrusted_anchor_not_preserved(self):
        markup,n,refs=server.render_annotated_html({'texteHtml':'<p><a href="javascript:alert(1)">article 3 de <span>la loi n°</span> 84-53</a></p>'},[])
        self.assertNotIn('javascript:',markup)
        self.assertIn('status-nonresolu',markup)
if __name__=='__main__':unittest.main()

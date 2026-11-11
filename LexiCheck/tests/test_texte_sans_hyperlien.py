import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

SOURCE='LEGIARTI000045213963'
TARGET='LEGIARTI000000002033'
BODY="Les règles générales relatives aux archives des collectivités territoriales et des groupements de collectivités territoriales sont fixées par les dispositions du II de l'article L. 212-4 et des articles L. 212-4-1 à L. 212-10 et L. 212-33 du code du patrimoine."

class Client:
    def article(self,ident):
        if ident==SOURCE:
            return {'id':SOURCE,'num':'L1421-1','etat':'VIGUEUR','dateDebut':'2022-02-23','dateFin':'2999-01-01','texteHtml':'<p>'+BODY+'</p>'}
        if ident==TARGET:
            return {'id':TARGET,'num':'L212-33','etat':'ABROGE','dateDebut':'2009-01-01','dateFin':'2018-01-01','texteHtml':'Texte abrogé'}
        raise AssertionError(ident)
    def call(self,route,params):
        if route=='consult/relatedLinksArticle':
            return {'liensCite':[{'id':TARGET,'name':'Code du patrimoine - art. L212-33','nature':'CODE'}]}
        if route=='consult/concordanceLinksArticle':return {'newTexts':[]}
        if route=='consult/getArticleByCid':return {'listArticle':[]}
        raise AssertionError(route)

class PlainTextRegression(unittest.TestCase):
    def test_screenshot_case(self):
        r=server.analyze(Client(),SOURCE,dt.date(2026,11,11))
        self.assertEqual(r['renvoisSurlignes'],2)
        self.assertEqual(r['comptes']['alerte'],1)
        self.assertIn('status-alerte',r['sourceHtmlAnnote'])
        self.assertIn('>L. 212-33</a>',r['sourceHtmlAnnote'])
        self.assertEqual(len(r['mentionsNonResolues']),3)
        self.assertIn('>L. 212-4</mark>',r['sourceHtmlAnnote'])
        self.assertIn('L. 212-4-1 à L. 212-10</mark>',r['sourceHtmlAnnote'])
        self.assertIn('Plage complexe', ' '.join(r['avertissements']))
        self.assertFalse(any('Aucun hyperlien' in x for x in r['avertissements']))

    def test_number_not_prefix(self):
        self.assertEqual([m.group() for m in server.LEGAL_NUM_RE.finditer('L. 212-4 et L. 212-4-1')],['L. 212-4','L. 212-4-1'])

    def test_ambiguous_must_not_claim_validity(self):
        checked=[{'id':TARGET,'titre':'L. 212-33','statut':'alerte','raison':'Abrogé'},
                 {'id':'LEGIARTI000000001234','titre':'L. 212-33','statut':'vigoureux','raison':'En vigueur'}]
        out, highlighted, _=server.render_annotated_html({'texteHtml':'<p>Article L. 212-33</p>'},checked)
        self.assertEqual(highlighted,0)
        self.assertIn('status-nonresolu',out)

if __name__=='__main__':unittest.main()

import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

DAY=dt.date(2026,11,11)
A='LEGIARTI000000000001'
B='LEGIARTI000000000002'
C='LEGIARTI000000000003'
TEXT='LEGITEXT000000000001'
S1='LEGISCTA000000000001'
S2='LEGISCTA000000000002'


def article(id,text,status='VIGUEUR',date='2022-01-01'):
    return {'id':id,'num':'12','etat':status,'dateDebut':date,
            'dateFin':'2999-01-01' if status=='VIGUEUR' else '2018-01-01',
            'texteHtml':f'<p>{text}</p>'}

class Stub:
    def __init__(self,items,tree=None):self.items=items;self.tree=tree or {}
    def article(self,id):return self.items[id]
    def call(self,route,payload):
        if route=='consult/legi/tableMatieres': return self.tree
        if route=='consult/getArticleByCid':return {'listArticle':[]}
        if route=='consult/concordanceLinksArticle':return {'newTexts':[]}
        if route=='consult/relatedLinksArticle':return {'liensCite':[]}
        raise AssertionError(route)

TREE={'title':'Code de démonstration','articles':[{'id':A,'etat':'VIGUEUR','num':'1'}],
      'sections':[{'cid':S1,'title':'Titre Ier : Généralités','articles':[{'id':B,'num':'10','etat':'VIGUEUR','dateDebut':'2022-01-01','dateFin':'2999-01-01'}],
        'sections':[{'cid':S2,'title':'Chapitre 1','articles':[{'id':C,'num':'20','etat':'VIGUEUR','dateDebut':'2021-01-01','dateFin':'2999-01-01'},{'id':A,'etat':'VIGUEUR'}]}]}]}

class V3Tests(unittest.TestCase):
    def test_code_exhaustive_and_unique(self):
        x=server.list_articles_in_tree(TREE,DAY)
        self.assertEqual(x['nombre'],3)
        self.assertEqual([a['id'] for a in x['articles']],[A,B,C])

    def test_section_descendants(self):
        x=server.list_articles_in_tree(TREE,DAY,S2)
        self.assertEqual([a['id'] for a in x['articles']],[C,A])
        self.assertRaises(server.APIError,server.list_articles_in_tree,TREE,DAY,'LEGISCTA000000000999')

    def test_parse_legifrance_section(self):
        self.assertEqual(server.parse_scope(f'https://www.legifrance.gouv.fr/codes/section_lc/{TEXT}/{S1}','section'),(TEXT,S1))
        self.assertRaises(server.APIError,server.parse_scope,TEXT,'section')

    def test_outline_response(self):
        out=server.get_outline(Stub({},TREE),TEXT,'code',DAY)
        self.assertEqual(out['textId'],TEXT)
        self.assertEqual(out['nombre'],3)

    def test_replacement_identical(self):
        cl=Stub({A:article(A,'Une référence est prévue.','ABROGE'), B:article(B,'Une référence est prévue.')})
        suggestion=server.propose_replacement(cl,cl.items[A],[{'id':B}],DAY)
        self.assertEqual(suggestion['statut'],'proposition')
        self.assertTrue(suggestion['identique'])
        self.assertEqual(suggestion['candidats'][0]['similarite'],100.0)

    def test_replacement_different(self):
        cl=Stub({A:article(A,'Article ancien, titre 2.','ABROGE'), B:article(B,'Article récent, titre 3.')})
        suggestion=server.propose_replacement(cl,cl.items[A],[{'id':B}],DAY)
        self.assertEqual(suggestion['statut'],'verification')
        self.assertFalse(suggestion['identique'])
        self.assertTrue(suggestion['candidats'][0]['extraitAncien'])

    def test_multiple_candidates_no_silent_pick(self):
        cl=Stub({A:article(A,'ancien','ABROGE'), B:article(B,'nouveau'), C:article(C,'nouveau 2')})
        suggestion=server.propose_replacement(cl,cl.items[A],[{'id':B},{'id':C}],DAY)
        self.assertEqual(suggestion['statut'],'ambigu')
        self.assertNotIn('id',suggestion)

    def test_replacement_not_in_force(self):
        cl=Stub({A:article(A,'ancien','ABROGE'),B:article(B,'nouveau','ABROGE')})
        self.assertEqual(server.propose_replacement(cl,cl.items[A],[{'id':B}],DAY)['statut'],'aucune')

    def test_bulk_limit(self):
        cl=Stub({A:article(A,'<a href="/codes/article_lc/'+B+'">renvoi</a>'), B:article(B,'texte')})
        result=server.analyze(cl,A,DAY,bulk=True)
        self.assertEqual(result['sourceHtmlAnnote'],'')
        self.assertEqual(result['comptes']['vigoureux'],1)

if __name__=='__main__':unittest.main()

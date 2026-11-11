"""Régressions du rapprochement indexation -> texte ; aucune PISTE réelle."""
import datetime as dt
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

SOURCE='LEGIARTI000044190643'
M16='LEGIARTI000046554767'
M17='LEGIARTI000045580612'
MINING_WRONG='LEGIARTI000000000014'
LAW='LEGIARTI000038922660'
CODE_ONLY='LEGIARTI000000000016'

DAY=dt.date(2026,11,11)

def art(id,num,title='',state='VIGUEUR',cid=None):
    o=dict(id=id,cid=cid or id,num=num,etat=state,
           dateDebut='2020-01-01',dateFin='2999-01-01',texteHtml='<p>Texte.</p>')
    if title:o['textTitles']=[{'titre':title}]
    return o

class Client:
    def __init__(self, txt, links, db):
        self.txt=txt;self.links=links;self.db=dict(db)
        self.db[SOURCE]=art(SOURCE,'L4141-2','Code général des collectivités territoriales')
        self.db[SOURCE]['texteHtml']='<p>'+txt+'</p>'
        self.gets=[];self.searches=[]
    def article(self,ident):
        self.gets.append(ident)
        return self.db[ident]
    def versions(self,cid):return []
    def search_article_number(self,number,code,day):
        self.searches.append((number,code))
        return []
    def search_loda_article_number(self,number,textnum,day,kind='loi'):
        self.searches.append((number,textnum,kind))
        return []
    def call(self,endpoint,body):
        if endpoint=='consult/relatedLinksArticle':return {'liensCite':self.links}
        if endpoint=='consult/concordanceLinksArticle':return {'newTexts':[]}
        raise AssertionError(endpoint)

class MetaMatching(unittest.TestCase):
    def test_html_text_unlinked_metadata_both_numbers(self):
        txt='Articles L. 611-16 et L. 611-17 du code minier.'
        links=[{'id':M16,'name':'Code minier (nouveau) - art. L611-16','nature':'CODE'},
               {'id':M17,'name':'Code minier (nouveau) - art. L611-17','nature':'CODE'},
               {'id':CODE_ONLY,'name':'Code minier (nouveau) - art. L611-30','nature':'CODE'}]
        c=Client(txt,links,{
            M16:art(M16,'L611-16','Code minier (nouveau)'),
            M17:art(M17,'L611-17','Code minier (nouveau)'),
            CODE_ONLY:art(CODE_ONLY,'L611-30','Code minier (nouveau)')})
        out=server.analyze(c,SOURCE,DAY)
        self.assertEqual({e['id'] for e in out['renvois']},{M16,M17})
        self.assertEqual({e['id'] for e in out['liensIndexationSeule']},{CODE_ONLY})
        self.assertIn('metadonnees_rapprochees',out['renvois'][0]['sources'])
        self.assertEqual(out['comptes']['vigoureux'],2)
        self.assertEqual(out['mentionsNonResolues'],[])
        self.assertIn('status-vigoureux',out['sourceHtmlAnnote'])
        self.assertFalse(c.searches, 'Une recherche NUM_ARTICLE redondante ne devrait pas être lancée')

    def test_index_name_without_number_uses_getarticle_metadata(self):
        c=Client('Article L. 611-16 du code minier.',
                 [{'id':M16,'name':'Lien de citation #42','nature':'CODE'}],
                 {M16:art(M16,'L611-16','Code minier (nouveau)')})
        out=server.analyze(c,SOURCE,DAY)
        self.assertEqual([e['id'] for e in out['renvois']],[M16])
        self.assertIn('status-vigoureux',out['sourceHtmlAnnote'])

    def test_wrong_code_not_matched_despite_equal_number(self):
        c=Client('Article L. 611-16 du code minier.',
                 [{'id':MINING_WRONG,'name':'Code du patrimoine - art. L611-16','nature':'CODE'}],
                 {MINING_WRONG:art(MINING_WRONG,'L611-16','Code du patrimoine')})
        out=server.analyze(c,SOURCE,DAY)
        self.assertEqual(out['renvois'],[])
        self.assertEqual([x['id'] for x in out['liensIndexationSeule']],[MINING_WRONG])
        self.assertIn('status-nonresolu',out['sourceHtmlAnnote'])
        self.assertTrue(any('L. 611-16' in u for u in out['mentionsNonResolues']))
        self.assertTrue(c.searches, 'La présence d’un mauvais lien indexé ne doit pas supprimer le fallback')

    def test_two_distinct_cids_ambiguous_remain_unresolved(self):
        c=Client('Article L. 611-16 du code minier.',
                 [{'id':M16,'name':'Code minier - art. L611-16','nature':'CODE'},
                  {'id':MINING_WRONG,'name':'Code minier - art. L611-16','nature':'CODE'}],
                 {M16:art(M16,'L611-16','Code minier (nouveau)'),
                  MINING_WRONG:art(MINING_WRONG,'L611-16','Code minier (nouveau)')})
        out=server.analyze(c,SOURCE,DAY)
        self.assertEqual(out['renvois'],[])
        self.assertEqual(len(out['liensIndexationSeule']),2)
        self.assertIn('status-nonresolu',out['sourceHtmlAnnote'])

    def test_indexed_law_not_manual_correction(self):
        old=art(LAW,'3','Loi n° 84-53 du 26 janvier 1984',state='ABROGE')
        old['dateFin']='2022-03-01'
        c=Client("En application de l'article 3 de la loi n° 84-53 du 26 janvier 1984.",
                 [{'id':LAW,'name':'LOI n°84-53 du 26 janvier 1984 - art. 3','nature':'LOI'}],
                 {LAW:old})
        out=server.analyze(c,SOURCE,DAY)
        self.assertEqual([r['id'] for r in out['renvois']],[LAW])
        self.assertEqual(out['renvois'][0]['statut'],'alerte')
        self.assertEqual(out['renvois'][0]['suggestion']['statut'],'aucune')
        self.assertNotIn('Sénat',str(out))
        self.assertIn('status-alerte',out['sourceHtmlAnnote'])
        self.assertFalse(c.searches)

    def test_indexed_law_same_article_other_law_not_accepted(self):
        c=Client("En application de l'article 3 de la loi n° 84-53 du 26 janvier 1984.",
                 [{'id':LAW,'name':'LOI n° 84-53 du 26 janvier 1984 - art. 3','nature':'LOI'}],
                 {LAW:art(LAW,'3','Loi n° 90-53 du 26 janvier 1990')})
        out=server.analyze(c,SOURCE,DAY)
        self.assertEqual(out['renvois'],[])
        self.assertIn('status-nonresolu',out['sourceHtmlAnnote'])

if __name__=='__main__':unittest.main()

"""Tests sans aucun cas de référence préparé : successeurs issus UNIQUEMENT de newTexts.
Les ID sont fictifs. Changer noms, codes ou numéros ne doit pas affecter l'algorithme.
"""
import datetime as dt
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

DAY=dt.date(2026,11,11)
A='LEGIARTI000000000011'
B='LEGIARTI000000000012'
C='LEGIARTI000000000013'
D='LEGIARTI000000000014'
E='LEGIARTI000000000015'
F='LEGIARTI000000000016'
CID='LEGIARTI000000000099'

def article(ident,txt='Même disposition juridique.', status='ABROGE', cid=None, num='7',
            start='1999-01-01', end=None):
    return {'id':ident,'cid':cid or ident,'num':num,'etat':status,
            'dateDebut':start,'dateFin':end or ('2999-01-01' if status=='VIGUEUR' else '2019-01-01'),
            'texteHtml':'<p>'+txt+'</p>', 'textTitles':[{'titre':'Loi n° 90-111 du 1er juin 1990'}]}

class Fake:
    def __init__(self,db,links=None,timeline=None):
        self.db=db;self.links=links or {};self.timeline=timeline or {};self.calls=[]
    def article(self,ident):
        self.calls.append(('article',ident))
        return self.db[ident]
    def versions(self,cid):
        self.calls.append(('versions',cid))
        return self.timeline.get(cid,[])
    def call(self,ep,body):
        self.calls.append((ep,body))
        if ep=='consult/concordanceLinksArticle':return self.links.get(body['articleId'],{'newTexts':[]})
        if ep=='consult/relatedLinksArticle':return {'liensCite':[]}
        raise AssertionError(ep)

class GenericConcordanceTests(unittest.TestCase):
    def test_simple_transfer_applies_to_any_article(self):
        c=Fake({A:article(A),B:article(B,status='VIGUEUR')},
               {A:{'newTexts':[{'id':B,'nature':'LODA','modifications':[{'type':'TRANSFERT'}]}]}})
        r=server.inspect_reference(c,{'id':A,'nature':'LODA','titre':'Article abrogé'},DAY)
        self.assertEqual(r['suggestion']['statut'],'proposition')
        self.assertEqual(r['suggestion']['id'],B)
        self.assertEqual(r['suggestion']['source'],'Concordances Légifrance (API)')
        self.assertEqual(r['concordances'][0]['parcours'],[A,B])

    def test_two_step_transfer_finds_current_successor(self):
        c=Fake({A:article(A),B:article(B,txt='Intermédiaire.'),C:article(C,status='VIGUEUR',txt='Nouvelle rédaction.')},
               {A:{'newTexts':[{'id':B}]},B:{'newTexts':[{'id':C}]}})
        r=server.inspect_reference(c,{'id':A},DAY)
        self.assertEqual(r['suggestion']['statut'],'verification')
        self.assertEqual(r['suggestion']['id'],C)
        self.assertEqual(r['concordances'][0]['parcours'],[A,B,C])

    def test_older_version_of_same_cid_has_concordance(self):
        c=Fake({A:article(A,cid=CID),B:article(B,cid=CID,start='2019-01-01'),C:article(C,status='VIGUEUR')},
               {B:{'newTexts':[{'id':C}]}},
               {CID:[{'id':A,'dateDebut':'1999-01-01','dateFin':'2019-01-01','etat':'MODIFIE'},
                     {'id':B,'dateDebut':'2019-01-01','dateFin':'2022-01-01','etat':'ABROGE'}]})
        r=server.inspect_reference(c,{'id':A},DAY)
        self.assertEqual(r['suggestion']['id'],C)
        self.assertIn(B,r['rechercheSuccesseur']['versionsExaminees'])

    def test_multiple_destinations_never_choose_automatically(self):
        c=Fake({A:article(A),B:article(B,status='VIGUEUR'),C:article(C,status='VIGUEUR')},
               {A:{'newTexts':[{'id':B},{'id':C}]}})
        r=server.inspect_reference(c,{'id':A},DAY)
        self.assertEqual(r['suggestion']['statut'],'ambigu')
        self.assertNotIn('id',r['suggestion'])
        self.assertEqual(len(r['suggestion']['candidats']),2)

    def test_backward_only_is_not_successor(self):
        c=Fake({A:article(A),B:article(B,status='VIGUEUR')},
               {A:{'newTexts':[],'oldTexts':[{'id':B}]}})
        r=server.inspect_reference(c,{'id':A},DAY)
        self.assertEqual(r['suggestion']['statut'],'aucune')
        self.assertNotIn('id',r['suggestion'])

    def test_same_cid_is_version_not_change_of_reference(self):
        c=Fake({A:article(A,cid=CID),B:article(B,status='VIGUEUR',cid=CID)},
               {A:{'newTexts':[{'id':B}]}})
        r=server.inspect_reference(c,{'id':A},DAY)
        # `versions` intentionally absent from API mock, link to same CID not a replacement.
        self.assertEqual(r['concordances'],[])
        self.assertEqual(r['suggestion']['statut'],'aucune')

    def test_old_reform_example_no_special_bypass(self):
        # Textes quelconques et aucune concordance : jamais de candidat inventé.
        a=article(A,'Temps de travail des agents.',num='3')
        c=Fake({A:a,B:article(B,status='VIGUEUR',num='L332-23')})
        r=server.inspect_reference(c,{'id':A},DAY)
        self.assertEqual(r['suggestion']['statut'],'aucune')
        self.assertFalse(any(x['id']==B for x in r['concordances']))

    def test_unavailable_successor_makes_search_partial(self):
        c=Fake({A:article(A)}, {A:{'newTexts':[{'id':B}]}})
        r=server.inspect_reference(c,{'id':A},DAY)
        self.assertTrue(r['rechercheSuccesseur']['partiel'])
        self.assertNotIn('id',r['suggestion'])

    def test_limit_never_claims_unique(self):
        original=server.CONCORDANCE_MAX_CALLS
        server.CONCORDANCE_MAX_CALLS=1
        try:
            c=Fake({A:article(A),B:article(B,status='VIGUEUR'),C:article(C,status='VIGUEUR')},
                   {A:{'newTexts':[{'id':B}]}}, {A:[{'id':C,'dateDebut':'2017-01-01'}]})
            r=server.inspect_reference(c,{'id':A},DAY)
            self.assertEqual(r['suggestion']['statut'],'ambigu')
            self.assertNotIn('id',r['suggestion'])
        finally:server.CONCORDANCE_MAX_CALLS=original

if __name__=='__main__':unittest.main()

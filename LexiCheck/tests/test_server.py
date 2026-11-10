import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

A = "LEGIARTI000006307920"
B = "LEGIARTI000033219357"
C = "LEGIARTI000006307893"


class FakeClient:
    def __init__(self, articles, versions=None, concordance=None):
        self.articles = articles
        self.versions = versions or {}
        self.concordance = concordance or {}
        self.calls = []

    def article(self, ident):
        self.calls.append(('article', ident))
        return self.articles[ident]

    def call(self, path, body):
        self.calls.append((path, body))
        if path == "consult/getArticleByCid":
            return self.versions.get(body['cid'], {'listArticle': []})
        if path == 'consult/concordanceLinksArticle':
            return self.concordance.get(body['articleId'], {'newTexts': []})
        if path == 'consult/relatedLinksArticle':
            return {'liensCite': [{'id': B, 'name': 'Article B', 'nature': 'CODE'}], 'liensCitePar':[{'id': C, 'name': 'À ne pas inclure'}]}
        raise AssertionError(path)


class RenvoisTests(unittest.TestCase):
    def setUp(self):
        self.day = dt.date(2026, 11, 10)

    def test_extract_html_and_indexed_not_reverse(self):
        source = {'id': A, 'texteHtml': '<p>Voir <a href="/codes/article_lc/'+B+'">article L. 123-1</a> et article R. 87-2.</p>'}
        refs, missing = server.extract_references(source, {'liensCite':[{'id':B,'name':'Article B','nature':'CODE'}], 'liensCitePar':[{'id': C}]})
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['id'], B)
        self.assertEqual(set(refs[0]['sources']), {'texte','indexation'})
        self.assertIn('article R. 87-2', missing)

    def test_current_article(self):
        c = FakeClient({B: {'id': B, 'cid': C,'etat':'VIGUEUR', 'dateDebut':'2020-01-01','dateFin':'2999-01-01'}})
        r = server.inspect_reference(c, {'id': B,'titre':'B', 'sources':['texte']}, self.day)
        self.assertEqual(r['statut'], 'vigoureux')
        self.assertFalse(any(k[0]=='consult/getArticleByCid' for k in c.calls))

    def test_old_version_still_current_by_cid(self):
        c=FakeClient({B:{'id':B,'cid':C,'etat':'MODIFIE','dateDebut':'2015-01-01','dateFin':'2020-01-01'}},
                     versions={C:{'listArticle':[{'cid':C,'articleVersions':[{'id':A,'etat':'VIGUEUR','dateDebut':'2020-01-01','dateFin':'2999-01-01'}]}]}})
        r=server.inspect_reference(c,{'id':B,'titre':'B','sources':['indexation']},self.day)
        self.assertEqual(r['statut'],'ancienne_version')
        self.assertEqual(r['versionActuelle']['id'],A)

    def test_abrogation_with_concordance(self):
        c=FakeClient({B:{'id':B,'cid':C,'etat':'ABROGE','dateDebut':'2000-01-01','dateFin':'2018-01-01'}},
                     concordance={B:{'newTexts':[{'id':A,'name':'Article A','modifications':[{'type':'TRANSFERT'}]}]}})
        r=server.inspect_reference(c,{'id':B,'titre':'B','sources':['indexation']},self.day)
        self.assertEqual(r['statut'],'alerte')
        self.assertEqual(r['concordances'][0]['id'],A)

    def test_no_version_does_not_prove_obsolescence(self):
        c=FakeClient({B:{'id':B,'cid':C,'etat':'MODIFIE','dateDebut':'2000-01-01','dateFin':'2018-01-01'}})
        r=server.inspect_reference(c,{'id':B,'titre':'B','sources':['texte']},self.day)
        self.assertEqual(r['statut'],'indetermine')

    def test_date_end_exclusive(self):
        self.assertFalse(server.active_on({'dateDebut':'2020-01-01','dateFin':'2026-11-10'},self.day))
        self.assertTrue(server.active_on({'dateDebut':'2020-01-01','dateFin':'2999-01-01'},self.day))

    def test_only_cites_outbound(self):
        c=FakeClient({A:{'id':A,'num':'123', 'etat':'VIGUEUR','texteHtml':'Sans renvoi.'}, B:{'id':B,'etat':'VIGUEUR','dateDebut':'2020-01-01','dateFin':'2999-01-01'}})
        res=server.analyze(c,A,self.day)
        self.assertEqual(len(res['renvois']),1)
        self.assertEqual(res['renvois'][0]['id'],B)
        self.assertNotIn(C,[x['id'] for x in res['renvois']])
        self.assertEqual(res['comptes']['vigoureux'],1)


if __name__ == '__main__':
    unittest.main()

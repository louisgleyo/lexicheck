import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

A = "LEGIARTI000006307920"
B = "LEGIARTI000033219357"
C = "LEGIARTI000006307893"
D = "LEGIARTI000049103824"


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
        c=FakeClient({A:{'id':A,'num':'123', 'etat':'VIGUEUR','dateDebut':'2020-01-01','dateFin':'2999-01-01','texteHtml':'Sans renvoi.'}, B:{'id':B,'etat':'VIGUEUR','dateDebut':'2020-01-01','dateFin':'2999-01-01'}})
        res=server.analyze(c,A,self.day)
        self.assertEqual(len(res['renvois']),1)
        self.assertEqual(res['renvois'][0]['id'],B)
        self.assertNotIn(C,[x['id'] for x in res['renvois']])
        self.assertEqual(res['comptes']['vigoureux'],1)

    def test_current_source_is_selected_using_cid(self):
        old = {'id': A, 'cid': C, 'num': '156', 'etat': 'MODIFIE',
               'dateDebut': '2000-01-01', 'dateFin': '2005-01-01',
               'texteHtml': '<p>Ancienne rédaction <a href="/loda/article_lc/'+B+'">ancien article</a></p>'}
        newest = {'id': D, 'cid': C, 'num': '156', 'etat': 'VIGUEUR',
                  'dateDebut': '2026-07-01', 'dateFin': '2999-01-01',
                  'texteHtml': '<p>Nouvelle rédaction sans lien.</p>'}
        client = FakeClient({A: old, D: newest}, versions={C: {'listArticle': [
            {'id': A, 'dateDebut': old['dateDebut'], 'dateFin': old['dateFin'], 'etat': 'MODIFIE'},
            {'cid': C, 'articleVersions': [{'id': D, 'etat': 'VIGUEUR', 'dateDebut': '2026-07-01', 'dateFin': '2999-01-01'}]}
        ]}})
        chosen, supplied, warnings = server.choose_source_version(client, A, self.day, 'actuel')
        self.assertEqual(chosen['id'], D)
        self.assertEqual(supplied['id'], A)
        self.assertTrue(warnings)
        self.assertIn(('article', D), client.calls)

    def test_analysis_uses_new_source_for_links(self):
        old = {'id': A, 'cid': C, 'num': '156', 'etat': 'MODIFIE',
               'dateDebut': '2000-01-01', 'dateFin': '2005-01-01',
               'texteHtml': '<p><a href="/loda/article_lc/'+B+'">Ancien renvoi</a></p>'}
        newest = {'id': D, 'cid': C, 'num': '156', 'etat': 'VIGUEUR',
                  'dateDebut': '2026-07-01', 'dateFin': '2999-01-01',
                  'texteHtml': '<p>Nouveau texte <a href="/loda/article_lc/'+B+'">Référence</a></p>'}
        target = {'id': B, 'cid': B, 'etat': 'VIGUEUR', 'dateDebut': '2010-01-01', 'dateFin': '2999-01-01'}
        cl = FakeClient({A: old, D: newest, B: target}, versions={C: {
            'listArticle': [{'id': D, 'etat': 'VIGUEUR', 'dateDebut': '2026-07-01', 'dateFin': '2999-01-01'}]}})
        out = server.analyze(cl, A, self.day)
        self.assertEqual(out['article']['id'], D)
        self.assertEqual(out['articleDemandee']['id'], A)
        self.assertTrue(out['versionChangee'])
        self.assertIn('Nouveau texte', out['sourceHtmlAnnote'])
        self.assertNotIn('Ancien renvoi', out['sourceHtmlAnnote'])
        self.assertIn('status-vigoureux', out['sourceHtmlAnnote'])
        self.assertIn(('consult/relatedLinksArticle', {'articleId': D}), cl.calls)

    def test_historical_mode_uses_exact_id(self):
        old={'id': A, 'cid': C, 'etat': 'MODIFIE','dateDebut': '2000-01-01',
             'dateFin': '2005-01-01', 'texteHtml': '<p>Rédaction historique.</p>'}
        cl=FakeClient({A: old})
        chosen,supplied,warnings=server.choose_source_version(cl,A,self.day,'historique')
        self.assertEqual(chosen['id'],A)
        self.assertEqual(supplied['id'],A)
        self.assertTrue(warnings)
        self.assertFalse(any(x[0]=='consult/getArticleByCid' for x in cl.calls))

    def test_default_current_fails_without_confirmed_version(self):
        old={'id': A,'cid': C,'etat': 'ABROGE','dateDebut':'2000-01-01','dateFin':'2005-01-01'}
        cl=FakeClient({A:old})
        with self.assertRaises(server.APIError) as err:
            server.choose_source_version(cl,A,self.day,'actuel')
        self.assertEqual(err.exception.status,422)
        self.assertIn('mode',err.exception.message)

    def test_markup_colors_and_escapes_unsafe_html(self):
        markup=(f'<p>Citer <a href="/codes/article_lc/{B}">article L. 5-1</a> '
                f'puis article R. 99-1. <script>alert(1)</script>'
                f'<img src="x" onerror="alert(2)"></p>')
        source={'id':A,'texteHtml':markup}
        results=[{'id':B,'statut':'alerte','raison':'Article abrogé', 'nature':'CODE'}]
        rendered,n,ids=server.render_annotated_html(source,results)
        self.assertIn('status-alerte',rendered)
        self.assertIn('status-nonresolu',rendered)
        self.assertIn('article R. 99-1',rendered)
        self.assertNotIn('<script',rendered)
        self.assertNotIn('alert(',rendered)
        self.assertNotIn('<img',rendered)
        self.assertNotIn('onerror',rendered)
        self.assertEqual(n,1)
        self.assertEqual(ids,{B})

    def test_markup_no_fake_highlighting_for_indexed_only(self):
        article={'id':A,'texteHtml':'<p>Du texte sans lien.</p>'}
        rendered,n,ids=server.render_annotated_html(article,[{'id':B,'statut':'alerte'}])
        self.assertNotIn('status-alerte',rendered)
        self.assertEqual(n,0)
        self.assertEqual(ids,set())

    def test_flat_list_article_versions(self):
        payload={'listArticle':[
            {'id':A,'dateDebut':'2010-01-01','dateFin':'2019-01-01'},
            {'cid':C,'articleVersions':[{'id':D,'dateDebut':'2020-01-01','dateFin':'2999-01-01'}]}
        ]}
        versions=server.flatten_versions(payload)
        self.assertEqual({x['id'] for x in versions},{A,D})

    def test_no_html_xss_from_link_text(self):
        a={'id':A,'texteHtml':f'<p><a href="javascript:evil_{B}">&lt;script&gt;hey&lt;/script&gt;</a></p>'}
        rendered,_,_=server.render_annotated_html(a,[{'id':B,'statut':'vigoureux','raison':'ok'}])
        self.assertNotIn('javascript:',rendered)
        self.assertNotIn('<script>',rendered)
        self.assertIn('&lt;script&gt;',rendered)


if __name__ == '__main__':
    unittest.main()

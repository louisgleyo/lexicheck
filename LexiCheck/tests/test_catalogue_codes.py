"""Vérifie les variantes officielles sans règles spéciales par code.

Les réponses JSON des tests d'intégration sont simulées : aucune clé PISTE.
"""
import datetime as dt
import sys
import threading
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from code_catalog import OFFICIAL_CODE_TITLES

DAY = dt.date(2026, 11, 11)

class CatalogueTests(unittest.TestCase):
    def test_catalogue_matches_108_official_titles(self):
        self.assertEqual(len(OFFICIAL_CODE_TITLES), 108)
        self.assertEqual(len(set(OFFICIAL_CODE_TITLES)), 108)

    def test_all_nouveau_forms_are_generated_without_exceptions(self):
        found = {c for c in OFFICIAL_CODE_TITLES if c.lower().endswith('(nouveau)')}
        self.assertEqual(found, {
            'Code forestier (nouveau)', 'Code minier (nouveau)',
            'Code de justice militaire (nouveau)'})
        for official in found:
            simple = official[:-len(' (nouveau)')]
            self.assertEqual(server.code_search_labels(simple)[:2], [official, simple])
            self.assertEqual(server.code_search_labels(official)[:2], [official, simple])
            self.assertTrue(server.code_title_verifies_reference(simple, official))
            self.assertFalse(server.code_title_verifies_reference(official, simple))

    def test_unknown_future_code_uses_general_fallback(self):
        self.assertEqual(server.code_search_labels('Code de la robotique'),
                         ['Code de la robotique', 'Code de la robotique (nouveau)'])
        self.assertTrue(server.code_title_verifies_reference('Code de la robotique', 'Code de la robotique (nouveau)'))

    def test_historic_versions_remain_distinct(self):
        distinct = [
            ('Code pénal', 'Code pénal (ancien)'),
            ('Code de commerce', 'Code de commerce (ancien)'),
            ('Code de la route', 'Code de la route (ancien)'),
            ('Code de procédure civile', 'Code de procédure civile (1807)'),
            ('Code rural et de la pêche maritime', 'Code rural (ancien)'),
            ('Code des marchés publics (édition 2001)', 'Code des marchés publics (édition 2006)'),
            ('Code général des impôts', 'Code général des impôts, annexe II'),
            ('Code forestier', 'Code forestier de Mayotte'),
        ]
        for left, right in distinct:
            with self.subTest(left=left, right=right):
                self.assertFalse(server.code_title_verifies_reference(left, right))
                self.assertFalse(server.code_title_verifies_reference(right, left))

    def test_unspecified_editons_are_not_arbitrarily_guessed(self):
        self.assertEqual(server.code_search_labels('Code des marchés publics'),
                         ['Code des marchés publics'])

    def test_specific_old_edition_never_expand_to_new(self):
        for code in ('Code pénal (ancien)', 'Code de commerce (ancien)',
                     'Code de la route (ancien)', 'Code des marchés publics (édition 2006)',
                     'Code de procédure civile (1807)'):
            self.assertEqual(server.code_search_labels(code), [code])

    def test_names_with_accents_and_punctuation_are_recognised(self):
        source={'textTitles':[{'titre':'Code civil'}]}
        examples=(
            ('articles L. 111-1 du code de l’entrée et du séjour des étrangers et du droit d’asile ;',
             'Code de l\'entrée et du séjour des étrangers et du droit d\'asile'),
            ('article L. 100-1 du code forestier (nouveau) ;', 'Code forestier (nouveau)'),
            ('article L. 100-1 du Code général des impôts, annexe II ;','Code général des impôts, annexe II'),
        )
        for clause, expected in examples:
            with self.subTest(clause=clause):
                p=clause.index('L. ')+len('L. 111-1') if 'L. 111-1' in clause else clause.index('L. ')+len('L. 100-1')
                self.assertEqual(server.code_for_mention(clause,p,source),expected)

    def test_source_code_isnt_infected_by_prior_explicit_code(self):
        source={'textTitles':[{'titre':'Code général des collectivités territoriales'}]}
        txt='8° Articles L. 611-16 et L. 611-17 du code minier ;\n9° Article L. 4433-15-1.'
        self.assertEqual(server.code_for_mention(txt,txt.index('L. 4433-15-1')+len('L. 4433-15-1'),source),
                         'Code général des collectivités territoriales')

class FakeClient(server.PisteClient):
    def __init__(self, title, returned):
        self._cache_lock=threading.Lock()
        self._lookup_cache={}; self._article_cache={}; self._lock=threading.Lock(); self.calls=[]
        self.title=title
        self.returned=returned
    def call(self, endpoint, body):
        self.calls.append((endpoint,body))
        if endpoint != 'search': raise AssertionError(endpoint)
        requested_code=body['recherche']['filtres'][0]['valeurs'][0]
        if requested_code!=self.title:
            return {'results':[]}
        return {'results':[{'sections':[{'extracts':[{'id': 'LEGIARTI000000000001'}]}]}]}
    def article(self, ident):
        return {'id':ident,'cid':'LEGICIDA00000000001','num':'L100-1',
                'textTitles':[{'titre':self.returned}],
                'etat':'VIGUEUR','dateDebut':'2020-01-01','dateFin':'2999-01-01'}

class GenericPisteTests(unittest.TestCase):
    def test_three_nouveau_codes_work_as_current_titles(self):
        for title in ('Code forestier (nouveau)', 'Code minier (nouveau)',
                      'Code de justice militaire (nouveau)'):
            with self.subTest(title=title):
                c=FakeClient(title,title)
                result=c.search_article_number('L100-1', title.removesuffix(' (nouveau)'), DAY)
                self.assertEqual(len(result),1)
                self.assertEqual(result[0]['textTitles'][0]['titre'],title)

    def test_false_historical_match_is_rejected(self):
        c=FakeClient('Code forestier (nouveau)','Code forestier (ancien)')
        self.assertEqual(c.search_article_number('L100-1','Code forestier',DAY),[])

if __name__=='__main__': unittest.main()

"""Régression pour L. 212-36 du Code du patrimoine : version mort-née vs abrogation."""
import datetime as dt
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

DAY = dt.date(2026, 11, 11)
SOURCE = 'LEGIARTI000006389283'
MODIFIE = 'LEGIARTI000033463442'
ABROGE = 'LEGIARTI000035110347'
CID = 'LEGIARTI000000004444'


class Client:
    search_article_number = server.PisteClient.search_article_number

    def __init__(self):
        self._cache_lock = threading.Lock()
        self._lookup_cache = {}
        self.calls = []
        self.db = {
            SOURCE: {'id': SOURCE, 'cid': SOURCE, 'num': 'L1421-3', 'etat': 'VIGUEUR',
                     'dateDebut': '2004-02-24', 'dateFin': '2999-01-01',
                     'texteHtml': '<p>Les articles L. 212-36 du code du patrimoine.</p>',
                     'textTitles': [{'titre': 'Code général des collectivités territoriales'}]},
            # Cette version a des dates de vigueur inversées et ne doit jamais être retenue.
            MODIFIE: {'id': MODIFIE, 'cid': CID, 'num': 'L212-36', 'etat': 'MODIFIE',
                      'dateDebut': '2018-01-01', 'dateFin': '2017-07-07',
                      'texteHtml': '<p>Modification non applicable.</p>',
                      'textTitles': [{'titre': 'Code du patrimoine'}]},
            ABROGE: {'id': ABROGE, 'cid': CID, 'num': 'L212-36', 'etat': 'ABROGE',
                     'dateDebut': '2004-02-24', 'dateFin': '2018-01-01',
                     'texteHtml': '<p>Texte abrogé.</p>',
                     'textTitles': [{'titre': 'Code du patrimoine'}]},
        }

    def article(self, ident):
        return self.db[ident]

    def versions(self, cid):
        return []

    def call(self, endpoint, request):
        self.calls.append((endpoint, request))
        if endpoint == 'search':
            if request['fond'] == 'CODE_ETAT':
                ids = [MODIFIE]
            elif request['fond'] == 'CODE_DATE':
                at = next(f['singleDate'] for f in request['recherche']['filtres'] if f['facette'] == 'DATE_VERSION')
                year = dt.datetime.fromtimestamp(at / 1000, tz=dt.timezone.utc).year
                ids = [ABROGE] if year == 2017 else []
            return {'results': [{'sections': [{'extracts': [{'id': ident} for ident in ids]}]}]}
        if endpoint == 'consult/relatedLinksArticle':
            return {'liensCite': []}
        if endpoint == 'consult/concordanceLinksArticle':
            return {'newTexts': []}
        raise AssertionError((endpoint, request))


class EndOfValidityTests(unittest.TestCase):
    def test_ambiguous_historical_index_resolved_by_historical_date(self):
        client = Client()
        candidates = client.search_article_number('L. 212-36', 'Code du patrimoine', DAY)
        self.assertEqual({a['id'] for a in candidates}, {MODIFIE, ABROGE})
        selected = max(candidates, key=lambda a: server.reference_version_rank(a, DAY))
        self.assertEqual(selected['id'], ABROGE)
        calls = [q for endpoint, q in client.calls if endpoint == 'search']
        self.assertEqual([q['fond'] for q in calls], ['CODE_DATE', 'CODE_ETAT', 'CODE_DATE', 'CODE_DATE'])
        self.assertFalse(server.effective_period(client.db[MODIFIE]))
        self.assertTrue(server.terminal_abrogation(client.db[ABROGE], DAY))

    def test_full_audit_reports_abrogation_not_indeterminate(self):
        out = server.analyze(Client(), SOURCE, DAY)
        self.assertEqual(out['comptes']['alerte'], 1)
        self.assertEqual(out['comptes']['indetermine'], 0)
        self.assertEqual(out['renvois'][0]['id'], ABROGE)
        self.assertIn('status-alerte', out['sourceHtmlAnnote'])

    def test_no_false_abrogation_without_explicit_evidence(self):
        client = Client()
        client.call = lambda ep, body: {'results': [{'sections': [{'extracts': [{'id': MODIFIE}]}]}]} if ep == 'search' else {'newTexts': []}
        candidates = client.search_article_number('L212-36', 'Code du patrimoine', DAY)
        chosen = max(candidates, key=lambda a: server.reference_version_rank(a, DAY))
        res = server.inspect_reference(client, {'id': chosen['id'], 'titre': 'Art. L212-36'}, DAY)
        self.assertNotEqual(res['statut'], 'alerte')


if __name__ == '__main__':
    unittest.main()

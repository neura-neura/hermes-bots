import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

class HistoryTests(unittest.TestCase):
    def fixture(self, ledger, day='2026-09-22', event='a-launch', url='https://other.example/alternate'):
        ed = {'publication_id':'test','date':day,'revision':1,
              'source_records':[{'id':1,'url':url,'title':'Different publisher wording','published_at':None,'retrieved_at':'2026-09-22T12:00:00+00:00'}],
              'stories':[{'id':'one','headline':'Different headline','label':'NEWS','paragraphs':['Different wording about the launch.'],'sources':[1],'event_id':event,'event_summary':'A launches on September 21'}]}
        snap = ledger.snapshot(day)
        ed['history_review'] = {'snapshot':snap['digest'],'reviewer':'test editor'}
        ed['stories'][0]['history_comparisons'] = [{'prior_ref':r['ref'],'relationship':'different_event','rationale':'Comparison for this test fixture.'} for r in snap['published']]
        return ed

    def test_next_day_and_alternate_publisher_same_event_blocked(self):
        from news_history import Ledger
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'history.db'; Ledger.initialize(p,'test')
            with Ledger(p,'test') as ledger:
                prior=self.fixture(ledger,day='2026-09-21')
                with ledger.conn:
                    ledger._record(prior,'published')  # synthetic successful historical fixture
                for url in ['https://other.example/alternate','https://second.example/report']:
                    ed=self.fixture(ledger,url=url)
                    ed['stories'][0]['paragraphs']=['Completely rewritten coverage at '+url]
                    with self.assertRaisesRegex(ValueError,'Repeated'):
                        ledger.check(ed)
                ed=self.fixture(ledger,event='renamed-event')
                ed['source_records'][0]['url']='https://third.example/news'
                ed['stories'][0]['paragraphs']=['Fresh paraphrase of yesterday, without any new fact.']
                ed['stories'][0]['history_comparisons'][0]['relationship']='same_event'
                with self.assertRaisesRegex(ValueError,'Repeated'):
                    ledger.check(ed)
                ed['stories'][0]['history_comparisons']=[]
                with self.assertRaisesRegex(ValueError,'comparison'):
                    ledger.check(ed)
                # A same-day manual revision is a different artifact, not next-day repetition.
                ed=self.fixture(ledger,day='2026-09-21'); ed['revision']=2
                ledger.check(ed)

    def test_meaningful_update_needs_new_facts_rationale_and_visible_reference(self):
        from news_history import Ledger
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'history.db'; Ledger.initialize(p,'test')
            with Ledger(p,'test') as ledger:
                with ledger.conn:
                    ledger._record(self.fixture(ledger,day='2026-09-21'),'published')
                ed=self.fixture(ledger); s=ed['stories'][0]
                s['paragraphs']=['A has now shipped to customers; prior report 2026-09-21/r1/one.']
                s['label']='ACTUALIZACIÓN'
                s['update']={'prior_refs':['2026-09-21/r1/one'],'new_facts':['Shipment began today, rather than the earlier announcement.'],'rationale':'The launch has become an actual shipment, confirmed by the supplier.'}
                ledger.check(ed)
                for key in ['rationale','new_facts','prior_refs']:
                    broken=copy.deepcopy(ed); del broken['stories'][0]['update'][key]
                    with self.assertRaises(ValueError): ledger.check(broken)
                broken=copy.deepcopy(ed); broken['stories'][0]['label']='NEWS'
                with self.assertRaises(ValueError): ledger.check(broken)
                broken=copy.deepcopy(ed); broken['stories'][0]['paragraphs']=['Different wording about the launch.']
                with self.assertRaises(ValueError): ledger.check(broken)

    def test_archive_commit_requires_validation_and_is_atomic(self):
        import hashlib, json
        import pymupdf
        from news_history import Ledger
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'history.db'; Ledger.initialize(p,'test')
            folder=Path(d)/'archive/2026/09/22/r1'; folder.mkdir(parents=True)
            with Ledger(p,'test') as ledger:
                self.assertTrue(hasattr(ledger, 'finalize'), 'No validated-archive commit gate')
                ed=self.fixture(ledger)
                (folder/'edition.json').write_text(json.dumps(ed))
                with self.assertRaises((ValueError, FileNotFoundError)):
                    ledger.finalize(folder,Path(d)/'archive')
                self.assertEqual(ledger.records('published'),[])
                from news_history import digest
                pdf=pymupdf.open(); page=pdf.new_page(); page.insert_text((40,40),'TEST ONLY: validated archive fixture'); pdf.save(folder/'test.pdf'); pdf.close()
                sha=hashlib.sha256((folder/'test.pdf').read_bytes()).hexdigest()
                manifest={'publication_id':'test','edition_date':ed['date'],'edition_revision':1,'pdf_filename':'test.pdf','pdf_hash':sha,'status':'COMPLETE_PDF_ONLY','story_count':1,'page_count':1,'qa_status':'PASS'}
                image=folder/'page.png'; image.write_bytes(b'Test visual evidence fixture, not a newspaper')
                visual={'status':'PASS','pdf_sha256':sha,'pages':[{'page':1,'reviewed':True,'images':[{'path':str(image),'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]}]}
                for name, value in [('manifest.json',manifest),('sources.json',ed['source_records']),('independent-qa.json',{'status':'FAIL','errors':['broken'],'pdf_sha256':sha,'edition_sha256':digest(ed),'page_count':1}),('visual-review.json',visual),('density-check.json',[{'page':1,'occupancy':.9}])]:
                    (folder/name).write_text(json.dumps(value))
                (folder/'production.log').write_text('Test archive fixture, not production.')
                with self.assertRaises(ValueError): ledger.finalize(folder,Path(d)/'archive')
                self.assertEqual(ledger.records('published'),[])
                (folder/'independent-qa.json').write_text(json.dumps({'status':'PASS','errors':[],'pdf_sha256':sha,'edition_sha256':digest(ed),'page_count':1}))
                ledger.finalize(folder,Path(d)/'archive')
                ledger.finalize(folder,Path(d)/'archive')
                self.assertEqual(len(ledger.records('published')),1)
                self.assertEqual(ledger.records('published')[0]['archive']['pdf_sha256'],sha)
                # Another publication cannot borrow this history.
                with self.assertRaises(ValueError): Ledger(p,'another-paper')

    def test_reusable_tools_fail_closed_before_rendering(self):
        import json, subprocess
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'broken'; folder.mkdir()
            ed={'language':'es','publication':'The K Times','date':'2026-09-22','revision':1,'stories':[]}
            model=folder/'edition.json'; model.write_text(json.dumps(ed))
            result=subprocess.run([sys.executable,str(root/'scripts/render_newspaper.py'),str(model),'--output-dir',str(Path(d)/'output')],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('history',result.stderr.lower(),result.stderr)
            self.assertFalse((Path(d)/'output').exists())
            result=subprocess.run([sys.executable,str(root/'scripts/verify_edition.py'),str(folder)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('history',result.stderr.lower(),result.stderr)

    def test_backfill_accepted_r5_only_and_idempotent(self):
        self.assertIsNotNone(importlib.util.find_spec('backfill_news_history'), 'Missing canonical archive migration')
        from backfill_news_history import backfill
        from news_history import Ledger
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'history.db'
            result=backfill(p)
            self.assertEqual(result['published_stories'],22)
            self.assertEqual(result['unique_sources'],23)
            backfill(p)
            with Ledger(p,'the-k-times') as ledger:
                rows=ledger.records('published')
                self.assertEqual(len(rows),22)
                self.assertEqual({r['revision'] for r in rows},{5})
                self.assertTrue(all(r['sources'][0]['retrieved_at'] for r in rows))
                self.assertTrue(all('published_at' in s for r in rows for s in r['sources']))

    def test_cli_snapshot_and_stats_readback(self):
        import json, subprocess
        root=Path(__file__).resolve().parents[1]
        script=str(root/'scripts/news_history.py')
        result=subprocess.run([sys.executable,script,'stats','--publication','the-k-times'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(result.stdout.strip(), 'Missing usable history CLI')
        stats=json.loads(result.stdout)
        # The durable ledger can grow as later editions are finalized; test the CLI
        # against the live ledger API instead of freezing the historic baseline count.
        from news_history import Ledger, ledger_path
        with Ledger(ledger_path('the-k-times'),'the-k-times') as ledger:
            published_rows=ledger.records('published')
            published_count=len(published_rows)
            expected_snapshot_count=sum(r['edition_date'] != '2026-09-22' for r in published_rows)
        self.assertEqual(stats['counts']['published'],published_count)
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'snapshot.json'
            result=subprocess.run([sys.executable,script,'snapshot','--publication','the-k-times','--date','2026-09-22','--output',str(target)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(target.read_text())
            self.assertEqual(len(data['published']),expected_snapshot_count)

    def test_compact_history_index_covers_all_records_and_matches_snapshot_digest(self):
        import json
        from news_history import Ledger
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'history.db'; Ledger.initialize(p,'test')
            with Ledger(p,'test') as ledger:
                for day in ('2026-09-20','2026-09-21','2026-09-22','2026-09-23'):
                    ed=self.fixture(ledger,day=day,event='launch-'+day,url='https://source.example/'+day)
                    with ledger.conn: ledger._record(ed,'published')
                full_snapshot = ledger.snapshot('2026-09-23')
                indexed = ledger.history_index('2026-09-23')
                paged_first = ledger.history_index('2026-09-23', offset=0, limit=2)
                paged_next = ledger.history_index('2026-09-23', offset=2, limit=2)
            self.assertEqual(indexed['count'],len(full_snapshot['published']))
            self.assertEqual(indexed['digest'],full_snapshot['digest'])
            self.assertEqual(paged_first['digest'],paged_next['digest'])
            self.assertEqual(paged_first['count'],paged_next['count'])
            self.assertEqual(paged_first['published'],indexed['published'][:2])
            self.assertEqual(paged_next['published'],indexed['published'][2:4])
            self.assertEqual({x['ref'] for x in indexed['published']},{x['ref'] for x in full_snapshot['published']})
            self.assertTrue(all({'event_id','event_summary','headline','content_fingerprint','source_urls'} <= x.keys() for x in indexed['published']))
            self.assertLess(len(json.dumps(indexed)),len(json.dumps(full_snapshot)))

    def test_stale_review_missing_database_and_selection_do_not_publish(self):
        from news_history import Ledger
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'history.db'
            with self.assertRaises(Exception): Ledger(p,'test')
            self.assertFalse(p.exists())
            Ledger.initialize(p,'test')
            with Ledger(p,'test') as ledger:
                ed=self.fixture(ledger)
                ledger.select(ed)
                self.assertEqual(len(ledger.records('selected')),1)
                self.assertEqual(ledger.records('published'),[])
                with ledger.conn: ledger._record(self.fixture(ledger,day='2026-09-21'),'published')
                with self.assertRaisesRegex(ValueError,'stale'): ledger.check(ed)
                prior_count=len(ledger.records('published'))
                with self.assertRaises(ValueError): ledger.select(ed)
                self.assertEqual(len(ledger.records('published')),prior_count)

    def test_duplicate_story_ids_and_empty_editions_rejected(self):
        from news_history import Ledger
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'history.db'; Ledger.initialize(p,'test')
            with Ledger(p,'test') as ledger:
                ed=self.fixture(ledger)
                duplicate=copy.deepcopy(ed['stories'][0]); duplicate['event_id']='another-event'
                ed['stories'].append(duplicate)
                with self.assertRaisesRegex(ValueError,'story IDs'): ledger.check(ed)
                ed['stories']=[]
                with self.assertRaisesRegex(ValueError,'nonempty'): ledger.check(ed)

    def test_published_revision_cannot_be_replaced_by_new_story_ids(self):
        from news_history import Ledger
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'history.db'; Ledger.initialize(p,'test')
            with Ledger(p,'test') as ledger:
                ed=self.fixture(ledger)
                with ledger.conn: ledger._record(ed,'published',archive={'edition_sha256':'original'})
                ed['stories'][0]['id']='new-id'
                with self.assertRaisesRegex(ValueError,'revision'):
                    with ledger.conn: ledger._record(ed,'published',archive={'edition_sha256':'changed'})
                self.assertEqual(len(ledger.records('published')),1)

    def test_durable_consulted_is_not_published(self):
        self.assertIsNotNone(importlib.util.find_spec('news_history'), 'Persistent news history module is missing')
        from news_history import Ledger
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'news.sqlite3'
            Ledger.initialize(path, 'test')
            ed = {'publication_id':'test','date':'2026-09-21','revision':1,
                  'source_records':[{'id':1,'url':'https://example.org/news?utm_source=test','title':'A launch','published_at':None,'retrieved_at':'2026-09-21T12:00:00+00:00'}],
                  'stories':[{'id':'one','headline':'A launch','paragraphs':['A launched today.'],'sources':[1],'event_id':'a-launch','event_summary':'A launches on September 21'}]}
            with Ledger(path, 'test') as ledger:
                ledger.consult(ed)
            with Ledger(path, 'test') as ledger:
                self.assertEqual(len(ledger.records('consulted')), 1)
                self.assertEqual(ledger.records('published'), [])
                ed['date']='2026-09-22'  # Consulting yesterday does not ban publication today.
                ed['history_review']={'snapshot':ledger.snapshot(ed['date'])['digest'],'reviewer':'editor'}
                ed['stories'][0]['history_comparisons']=[]
                ledger.check(ed)

if __name__ == '__main__':
    unittest.main()

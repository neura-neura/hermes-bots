"""Durable, publication-isolated editorial history. No network, rendering or printing."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def canonical_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid public source URL')
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/', urlencode(sorted(query)), ''))


def story_record(ed, story):
    date.fromisoformat(ed['date'])
    if not isinstance(ed['revision'], int) or ed['revision'] < 1:
        raise ValueError('Invalid revision')
    for key in ('id', 'headline', 'event_id', 'event_summary'):
        if not isinstance(story.get(key), str) or not story[key].strip():
            raise ValueError('Missing story ' + key)
    sources = {s['id']: s for s in ed['source_records']}
    provenance = []
    for source_id in story['sources']:
        s = dict(sources[source_id])
        s['canonical_url'] = canonical_url(s['url'])
        if not s.get('title') or not s.get('retrieved_at') or 'published_at' not in s:
            raise ValueError('Source requires title, retrieved_at and published_at (null if unknown)')
        if datetime.fromisoformat(s['retrieved_at']).tzinfo is None:
            raise ValueError('Retrieval timestamp needs timezone')
        provenance.append(s)
    if not provenance:
        raise ValueError('Story has no provenance')
    content = ' '.join(story['paragraphs'])
    return {'ref': f"{ed['date']}/r{ed['revision']}/{story['id']}",
            'edition_date': ed['date'], 'revision': ed['revision'], 'story_id': story['id'],
            'event_id': story['event_id'], 'event_summary': story['event_summary'],
            'headline': story['headline'], 'content_fingerprint': digest(re.sub(r'\s+', ' ', content).strip().casefold()),
            'sources': provenance, 'story': story}


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_archive(folder, ed, archive_root, legacy=False):
    import pymupdf
    folder = Path(folder).resolve()
    expected = Path(archive_root).resolve() / ed['date'].replace('-', '/') / ('r' + str(ed['revision']))
    if folder != expected:
        raise ValueError('Not the configured publication/date/revision archive')
    load = lambda name: json.loads((folder / name).read_text())
    m, qa, visual = load('manifest.json'), load('independent-qa.json'), load('visual-review.json')
    pdf = folder / m['pdf_filename']
    if pdf.parent != folder or pdf.suffix != '.pdf':
        raise ValueError('Invalid archived PDF filename')
    sha = file_sha(pdf)
    if (m['publication_id'] != ed['publication_id'] or m['edition_date'] != ed['date'] or
        m['edition_revision'] != ed['revision'] or m['story_count'] != len(ed['stories']) or
        m.get('status') not in ('COMPLETE', 'COMPLETE_PDF_ONLY', 'ARCHIVED', 'PRINT_FAILED') or
        m.get('qa_status') != 'PASS' or m.get('errors') or m['pdf_hash'].removeprefix('sha256:') != sha):
        raise ValueError('Manifest is not a successful matching archive')
    if (qa.get('status') != 'PASS' or qa.get('errors') or qa.get('pdf_sha256') != sha or
        (not legacy and qa.get('edition_sha256') != digest(ed))):
        raise ValueError('Independent validation missing, failed or stale')
    with pymupdf.open(pdf) as doc:
        pages = len(doc)
    if qa['page_count'] != pages or m['page_count'] != pages:
        raise ValueError('Archive page count mismatch')
    density = load('density-check.json')
    if legacy and isinstance(density, dict):
        if density.get('failures'):
            raise ValueError('Historical density check failed')
        density = [{'page': p['page'], 'occupancy': sum(c['used']/c['height'] for c in p['columns'])/len(p['columns'])}
                   for p in load('column-density.json')]
    if len(density) != pages or any(p['occupancy'] < .72 for p in density):
        raise ValueError('Density validation failed')
    if (visual.get('status') not in ('PASS', 'PASS_WITH_NOTED_WHITESPACE') or visual.get('pdf_sha256') != sha or
        len(visual['pages']) != pages or {p['page'] for p in visual['pages']} != set(range(1, pages + 1))):
        raise ValueError('Visual approval is missing or stale')
    for page in visual['pages']:
        if page.get('reviewed') is not True or not page.get('images'):
            raise ValueError('Unreviewed page')
        for image in page['images']:
            if file_sha(image['path']) != image['sha256']:
                raise ValueError('Visual evidence hash mismatch')
    if not (folder / 'production.log').read_text().strip():
        raise ValueError('Missing archive log')
    sources = load('sources.json')
    if not legacy and sources != ed['source_records']:
        raise ValueError('Archived sources differ from checked edition')
    return {'folder': str(folder), 'pdf_sha256': sha, 'edition_sha256': digest(ed),
            'manifest_sha256': file_sha(folder / 'manifest.json'),
            'sources_sha256': file_sha(folder / 'sources.json')}


def publication_config(publication_id):
    import yaml
    if not isinstance(publication_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', publication_id):
        raise ValueError('Persistent history requires a valid publication_id')
    config = yaml.safe_load((ROOT / 'profiles' / publication_id / 'config.yaml').read_text())
    if config['publication']['id'] != publication_id:
        raise ValueError('History publication configuration mismatch')
    return config


def ledger_path(publication_id):
    publication_config(publication_id)
    # A missing DB is an error, NEVER permission to silently forget prior news.
    return ROOT / 'profiles' / publication_id / 'news-history.sqlite3'


def require_history(ed, select=False, folder=None):
    publication_id = ed.get('publication_id')
    if not publication_id and folder:
        manifest = Path(folder) / 'manifest.json'
        if manifest.exists():
            publication_id = json.loads(manifest.read_text()).get('publication_id')
    with Ledger(ledger_path(publication_id), publication_id) as ledger:
        if folder:
            rows = ledger.records('published')
            matching = [r for r in rows if r.get('archive', {}).get('folder') == str(Path(folder).resolve())]
            if matching and len(matching) == len(ed['stories']):
                archived = matching[0]['archive']
                if archived.get('original_edition_sha256', archived['edition_sha256']) == digest(ed):
                    manifest = json.loads((Path(folder) / 'manifest.json').read_text())
                    if file_sha(Path(folder) / manifest['pdf_filename']) == archived['pdf_sha256']:
                        return {'status':'PASS_ALREADY_PUBLISHED','edition_sha256':digest(ed)}
        if not ed.get('stories'):
            raise ValueError('Persistent news history requires nonempty stories')
        return ledger.select(ed) if select else ledger.check(ed)


class Ledger:
    @staticmethod
    def initialize(path, publication):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ValueError('Refusing to replace existing ledger')
        with sqlite3.connect(path) as conn:
            conn.executescript('CREATE TABLE metadata (publication TEXT NOT NULL, version INTEGER NOT NULL);'
                               'CREATE TABLE records (ref TEXT, status TEXT CHECK(status IN (\'consulted\',\'selected\',\'published\')), payload TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(ref,status));')
            conn.execute('INSERT INTO metadata VALUES (?,1)', (publication,))

    def __init__(self, path, publication):
        self.conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=30)
        self.publication = publication
        if self.conn.execute('SELECT publication,version FROM metadata').fetchall() != [(publication, 1)]:
            self.conn.close()
            raise ValueError('Wrong publication or ledger schema')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    def records(self, status):
        """Return decoded records in stable ref order."""
        return [json.loads(row[0]) for row in self.conn.execute('SELECT payload FROM records WHERE status=? ORDER BY ref', (status,))]

    def history_records(self, edition_date):
        """Read published records in one deterministic scan, optionally scoped by date."""
        rows = []
        cursor = self.conn.execute('SELECT payload FROM records WHERE status=? ORDER BY ref', ('published',))
        for (payload,) in cursor:
            row = json.loads(payload)
            if row['edition_date'] != edition_date:
                rows.append(row)
        return rows

    def _history_views(self, edition_date):
        """Compute canonical digest/count and compact index from one records scan."""
        digest_rows = []
        index_rows = []
        cursor = self.conn.execute('SELECT payload FROM records WHERE status=? ORDER BY ref', ('published',))
        for (payload,) in cursor:
            row = json.loads(payload)
            if row['edition_date'] == edition_date:
                continue
            digest_rows.append(row)
            index_rows.append({
                'ref': row['ref'], 'event_id': row['event_id'],
                'event_summary': row['event_summary'], 'headline': row['headline'],
                'content_fingerprint': row['content_fingerprint'],
                'source_urls': sorted({s['canonical_url'] for s in row['sources']}),
            })
        canonical_digest = digest(digest_rows)
        full = {'publication_id': self.publication, 'edition_date': edition_date,
                'digest': canonical_digest, 'published': digest_rows}
        index = {'publication_id': self.publication, 'edition_date': edition_date,
                 'digest': canonical_digest, 'count': len(digest_rows), 'published': index_rows}
        return full, index

    def snapshot(self, edition_date):
        """Canonical full history snapshot; chunkable on disk by callers."""
        return self._history_views(edition_date)[0]

    def history_index(self, edition_date, offset=0, limit=None):
        """Compact duplicate-detection index; digest/count cover full history even when paginated."""
        if offset < 0 or (limit is not None and limit < 1):
            raise ValueError('History-index offset must be nonnegative and limit positive')
        rows = []
        count = 0
        canonical_digest = hashlib.sha256()
        first = True
        canonical_digest.update(b'[')
        cursor = self.conn.execute('SELECT payload FROM records WHERE status=? ORDER BY ref', ('published',))
        for (payload,) in cursor:
            record = json.loads(payload)
            if record['edition_date'] == edition_date:
                continue
            if not first:
                canonical_digest.update(b',')
            first = False
            canonical_digest.update(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
            if count >= offset and (limit is None or len(rows) < limit):
                rows.append({
                    'ref': record['ref'], 'event_id': record['event_id'],
                    'event_summary': record['event_summary'], 'headline': record['headline'],
                    'content_fingerprint': record['content_fingerprint'],
                    'source_urls': sorted({s['canonical_url'] for s in record['sources']}),
                })
            count += 1
        canonical_digest.update(b']')
        return {'publication_id': self.publication, 'edition_date': edition_date,
                'digest': canonical_digest.hexdigest(), 'count': count,
                'offset': offset, 'limit': limit, 'published': rows}

    def _record(self, ed, status, archive=None):
        if ed['publication_id'] != self.publication:
            raise ValueError('Publication mismatch')
        rows = [story_record(ed, s) for s in ed['stories']]
        if status == 'published':
            prior = [r for r in self.records('published') if r['edition_date'] == ed['date'] and r['revision'] == ed['revision']]
            if prior and ({r['ref'] for r in prior} != {r['ref'] for r in rows} or
                          any(r.get('archive') != archive for r in prior)):
                raise ValueError('Published revision is immutable; create a new revision')
        for row in rows:
            if archive:
                row['archive'] = archive
            key = row['ref']
            if status == 'consulted':
                key += '@' + digest(row)  # Preserve successive observations, not only the last draft.
            if status == 'published':
                old = self.conn.execute('SELECT payload FROM records WHERE ref=? AND status=?', (key, status)).fetchone()
                if old and json.loads(old[0]) != row:
                    raise ValueError('Published revision is immutable; create a new revision')
            self.conn.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',
                              (key, status, json.dumps(row, ensure_ascii=False, sort_keys=True), datetime.now(timezone.utc).isoformat()))
        return rows

    def select(self, ed):
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            result = self.check(ed)
            self._record(ed, 'consulted')
            self._record(ed, 'selected')
            return result

    def finalize(self, folder, archive_root):
        folder = Path(folder).resolve()
        ed = json.loads((folder / 'edition.json').read_text())
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            approval = validate_archive(folder, ed, archive_root)
            self.check(ed)  # Recheck latest history inside the publication transaction.
            rows = self._record(ed, 'published', archive=approval)
        return {'status': 'PUBLISHED', 'stories': len(rows), 'archive': approval}

    def consult(self, ed):
        with self.conn:
            return self._record(ed, 'consulted')

    def check(self, ed):
        if ed['publication_id'] != self.publication:
            raise ValueError('Publication mismatch')
        if not ed.get('stories'):
            raise ValueError('History requires nonempty stories')
        if len({s['id'] for s in ed['stories']}) != len(ed['stories']):
            raise ValueError('Duplicate story IDs in edition')
        if len({s['id'] for s in ed['source_records']}) != len(ed['source_records']):
            raise ValueError('Duplicate source IDs in edition')
        snap = self.snapshot(ed['date'])
        review = ed.get('history_review', {})
        if review.get('snapshot') != snap['digest'] or not review.get('reviewer'):
            raise ValueError('Missing or stale editorial history review')
        seen = set()
        for story in ed['stories']:
            row = story_record(ed, story)
            if row['event_id'] in seen:
                raise ValueError('Repeated event within edition; consolidate the package')
            seen.add(row['event_id'])
            comparisons = story.get('history_comparisons', [])
            by_ref = {c['prior_ref']: c for c in comparisons}
            if len(by_ref) != len(comparisons) or set(by_ref) != {r['ref'] for r in snap['published']}:
                raise ValueError('Missing or duplicate editorial comparison against published history')
            matches = []
            urls = {s['canonical_url'] for s in row['sources']}
            for prior in snap['published']:
                c = by_ref[prior['ref']]
                if c.get('relationship') not in ('same_event', 'different_event') or not c.get('rationale', '').strip():
                    raise ValueError('Every comparison needs relationship and factual rationale')
                if (row['event_id'] == prior['event_id'] or
                    row['content_fingerprint'] == prior['content_fingerprint'] or
                    urls.intersection(s['canonical_url'] for s in prior['sources']) or
                    c['relationship'] == 'same_event'):
                    matches.append(prior)
            update = story.get('update')
            if matches or update:
                prior_refs = {p['ref'] for p in matches}
                visible = ' '.join(story['paragraphs']) + ' ' + story.get('deck', '')
                if (not matches or not isinstance(update, dict) or
                    set(update.get('prior_refs', [])) != prior_refs or
                    not update.get('rationale', '').strip() or
                    not isinstance(update.get('new_facts'), list) or not update['new_facts'] or
                    not all(isinstance(f, str) and f.strip() for f in update['new_facts']) or
                    'ACTUALIZACIÓN' not in story.get('label', '').upper() or
                    not all(ref in visible for ref in prior_refs) or
                    any(row['content_fingerprint'] == p['content_fingerprint'] for p in matches)):
                    raise ValueError('Repeated published news requires a labelled factual update, prior references and rationale: ' + ', '.join(prior_refs))
        return {'status': 'PASS', 'snapshot': snap['digest'], 'edition_sha256': digest(ed)}


def main(argv=None):
    import argparse
    import os
    import tempfile
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('snapshot', 'history-index', 'stats'):
        p = sub.add_parser(command)
        p.add_argument('--publication', required=True)
        p.add_argument('--output')
        if command in ('snapshot', 'history-index'):
            p.add_argument('--date', required=True)
        if command == 'snapshot':
            p.add_argument('--offset', type=int, default=0)
            p.add_argument('--limit', type=int)
        if command == 'history-index':
            p.add_argument('--offset', type=int, default=0)
            p.add_argument('--limit', type=int)
    for command in ('consult', 'check', 'finalize'):
        p = sub.add_parser(command)
        p.add_argument('input', help='Edition/research JSON, or archive directory for finalize')
        p.add_argument('--output')
    args = parser.parse_args(argv)
    if args.command in ('snapshot', 'history-index', 'stats'):
        publication = args.publication
    else:
        model = Path(args.input) / 'edition.json' if args.command == 'finalize' else Path(args.input)
        ed = json.loads(model.read_text())
        publication = ed['publication_id']
    with Ledger(ledger_path(publication), publication) as ledger:
        if args.command == 'snapshot':
            date.fromisoformat(args.date)
            full = ledger.snapshot(args.date)
            offset = args.offset
            if offset < 0 or (args.limit is not None and args.limit < 1):
                raise ValueError('Snapshot offset must be nonnegative and limit positive')
            rows = full['published']
            result = dict(full, count=len(rows), offset=offset,
                          limit=args.limit, published=rows[offset:offset + args.limit if args.limit else None])
        elif args.command == 'history-index':
            date.fromisoformat(args.date)
            offset = args.offset
            if offset < 0 or (args.limit is not None and args.limit < 1):
                raise ValueError('History-index offset must be nonnegative and limit positive')
            result = ledger.history_index(args.date, offset=offset, limit=args.limit)
            rows = result['published']
            full_count = result['count']
            if args.output:
                # Stream the compact index to disk to keep even large histories out of RAM/output buffers.
                target = Path(args.output).resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temp = tempfile.mkstemp(prefix='.history-index-', dir=target.parent)
                try:
                    with os.fdopen(fd, 'w') as stream:
                        stream.write('{\n  "publication_id": ' + json.dumps(result['publication_id']) +
                                     ',\n  "edition_date": ' + json.dumps(result['edition_date']) +
                                     ',\n  "digest": ' + json.dumps(result['digest']) +
                                     ',\n  "count": ' + str(full_count) +
                                     ',\n  "offset": ' + str(offset) +
                                     ',\n  "limit": ' + json.dumps(args.limit) +
                                     ',\n  "published": [\n')
                        for i, row in enumerate(rows):
                            if i: stream.write(',\n')
                            stream.write('    ' + json.dumps(row, ensure_ascii=False, separators=(',', ':')))
                        stream.write('\n  ]\n}\n')
                        stream.flush(); os.fsync(stream.fileno())
                    os.replace(temp, target)
                finally:
                    if os.path.exists(temp): os.unlink(temp)
                print(json.dumps({'output':str(target),'status':'OK','count':full_count,'digest':result['digest'],'offset':offset,'returned':len(rows)}))
                return
        elif args.command == 'stats':
            rows = ledger.records('published')
            result = {'publication_id':publication, 'ledger':str(ledger_path(publication)),
                      'integrity':ledger.conn.execute('PRAGMA integrity_check').fetchone()[0],
                      'counts':{s:len(ledger.records(s)) for s in ('consulted','selected','published')},
                      'editions':sorted({f"{r['edition_date']}/r{r['revision']}" for r in rows}),
                      'unique_sources':len({s['canonical_url'] for r in rows for s in r['sources']}),
                      'event_identities':len({r['event_id'] for r in rows})}
        elif args.command == 'consult':
            result = {'status':'CONSULTED', 'observations':len(ledger.consult(ed))}
        elif args.command == 'check':
            result = ledger.check(ed)
        else:
            result = ledger.finalize(args.input, publication_config(publication)['archive']['root'])
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        target = Path(args.output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.history-', dir=target.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        print(json.dumps({'output':str(target), 'status':result.get('status','OK')}))
    else:
        print(text, end='')


if __name__ == '__main__':
    main()

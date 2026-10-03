import json
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EDITION = ROOT / 'newspapers/the-k-times/2026/09/28/r2/edition.json'
STORY_ID = 'so-kuramoto-obituary'


class NewspaperLayoutTests(unittest.TestCase):
    def test_early_image_stays_with_its_story_when_column_is_too_short(self):
        edition = json.loads(EDITION.read_text())
        story = next(s for s in edition['stories'] if s['id'] == STORY_ID)
        story['image_position'] = 'before'
        css = (ROOT / 'templates/flow_newspaper.css').read_text()
        script = (ROOT / 'templates/flow_newspaper.js').read_text()
        payload = json.dumps(edition, ensure_ascii=False).replace('</', '<\\/')
        html = ('<!doctype html><html lang="es"><head><meta charset="utf-8"><style>'
                + css + '</style></head><body><script>' + script
                + '\npaginate(' + payload + ');</script></body></html>')
        with tempfile.TemporaryDirectory() as tmp:
            page_path = Path(tmp) / 'fixture.html'
            page_path.write_text(html)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                page = browser.new_page()
                page.goto(page_path.as_uri())
                page.wait_for_function('window.paginationDone')
                colocated = page.evaluate('''(id) => [...document.querySelectorAll('.page')].some(p =>
                  p.querySelector('.photo[data-story="' + id + '"]') &&
                  p.querySelector('.bodytext[data-story="' + id + '"]'))''', STORY_ID)
                browser.close()
        self.assertTrue(colocated, 'The early-positioned image became detached from all of its body text')


if __name__ == '__main__':
    unittest.main()

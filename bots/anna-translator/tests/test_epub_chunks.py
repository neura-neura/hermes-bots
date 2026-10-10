import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

SCRIPT = Path(__file__).parents[1] / "skills/web/web-content-translation-epub/scripts/epub_chunks.py"
spec = importlib.util.spec_from_file_location("epub_chunks", SCRIPT)
epub_chunks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(epub_chunks)


def make_epub(path):
    container = """<?xml version='1.0'?>
<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container' version='1.0'>
 <rootfiles><rootfile full-path='EPUB/content.opf' media-type='application/oebps-package+xml'/></rootfiles>
</container>"""
    opf = """<?xml version='1.0'?>
<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>
 <metadata><title>Book</title><language>en</language></metadata>
 <manifest>
  <item id='c1' href='chapter.xhtml' media-type='application/xhtml+xml'/>
  <item id='img' href='cover.jpg' media-type='image/jpeg'/>
 </manifest><spine><itemref idref='c1'/></spine>
</package>"""
    chapter = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns='http://www.w3.org/1999/xhtml'><body><h1 id='top'>Hello world</h1><p>First <em>important</em> paragraph.</p><a href='#top'>Back</a><img src='cover.jpg' alt='Cover'/></body></html>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("EPUB/content.opf", opf)
        z.writestr("EPUB/chapter.xhtml", chapter)
        z.writestr("EPUB/cover.jpg", b"jpeg-bytes")


def test_prepare_and_rebuild_preserve_package_and_require_full_coverage(tmp_path):
    source = tmp_path / "book.epub"
    work = tmp_path / "work"
    output = tmp_path / "book-es.epub"
    make_epub(source)

    summary = epub_chunks.prepare(source, work, max_words=2)
    assert summary["spine_documents"] == 1
    assert summary["segments"] >= 4
    assert summary["chunks"] >= 2

    chunks = [json.loads(p.read_text()) for p in sorted((work / "chunks").glob("*.json"))]
    ids = [item["id"] for chunk in chunks for item in chunk["items"]]
    partial = {ids[0]: "Hola mundo"}
    (work / "translations/partial.json").write_text(json.dumps(partial))
    with pytest.raises(ValueError, match="Missing translations"):
        epub_chunks.rebuild(work, output, "es")

    translations = {item["id"]: f"ES:{item['text']}" for chunk in chunks for item in chunk["items"]}
    (work / "translations/complete.json").write_text(json.dumps(translations))
    (work / "translations/partial.json").unlink()
    result = epub_chunks.rebuild(work, output, "es")
    assert result["translated_segments"] == len(ids)

    with zipfile.ZipFile(output) as z:
        assert z.read("mimetype") == b"application/epub+zip"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert z.read("EPUB/cover.jpg") == b"jpeg-bytes"
        rendered = z.read("EPUB/chapter.xhtml").decode()
        assert "ES:Hello world" in rendered and "ES:important" in rendered
        assert 'href="#top"' in rendered and 'src="cover.jpg"' in rendered
        package = z.read("EPUB/content.opf").decode()
        assert ">es<" in package

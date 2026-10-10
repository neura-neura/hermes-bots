#!/usr/bin/env python3
"""Prepare and rebuild complete EPUB translations without losing package assets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil

import xml.etree.ElementTree as ET
import zipfile

TEXT_FIELDS = ("text", "tail")
SKIP_TAGS = {"script", "style"}


def local(tag):
    return tag.rsplit("}", 1)[-1]


def safe_extract(epub: Path, destination: Path):
    with zipfile.ZipFile(epub) as archive:
        for info in archive.infolist():
            rel = PurePosixPath(info.filename)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("Unsafe EPUB member path")
            target = destination.joinpath(*rel.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def container_opf(package: Path) -> Path:
    tree = ET.parse(package / "META-INF/container.xml")
    roots = [x for x in tree.getroot().iter() if local(x.tag) == "rootfile"]
    if not roots or not roots[0].get("full-path"):
        raise ValueError("EPUB container has no package document")
    candidate = (package / PurePosixPath(roots[0].get("full-path"))).resolve()
    if not candidate.is_relative_to(package.resolve()):
        raise ValueError("EPUB rootfile escapes package")
    return candidate


def reading_order(opf: Path, package: Path):
    root = ET.parse(opf).getroot()
    manifest = {x.get("id"): x for x in root.iter() if local(x.tag) == "item"}
    result = []
    for ref in (x for x in root.iter() if local(x.tag) == "itemref"):
        item = manifest.get(ref.get("idref"))
        if item is not None and item.get("media-type") in ("application/xhtml+xml", "text/html"):
            candidate = (opf.parent / PurePosixPath(item.get("href"))).resolve()
            if not candidate.is_relative_to(package.resolve()):
                raise ValueError("EPUB manifest path escapes package")
            if not candidate.is_file():
                raise ValueError("EPUB manifest resource is missing: " + item.get("href", ""))
            result.append(candidate)
    return result


def slots(tree):
    for element_index, element in enumerate(tree.getroot().iter()):
        if local(element.tag) in SKIP_TAGS:
            continue
        for field in TEXT_FIELDS:
            value = getattr(element, field)
            if value and value.strip():
                yield element_index, field, value
        for name in ("alt", "title", "aria-label"):
            value = element.attrib.get(name)
            if value and value.strip():
                yield element_index, "attr:" + name, value


def word_count(text):
    return len(re.findall(r"\w+(?:[’'-]\w+)*", text, flags=re.UNICODE))


def prepare(source, work, max_words=1200):
    source, work = Path(source).resolve(), Path(work).resolve()
    if not zipfile.is_zipfile(source):
        raise ValueError("Input is not a valid EPUB/ZIP package")
    if work.exists():
        shutil.rmtree(work)
    package = work / "package"
    chunks_dir = work / "chunks"
    translations = work / "translations"
    package.mkdir(parents=True)
    chunks_dir.mkdir()
    translations.mkdir()
    safe_extract(source, package)
    opf = container_opf(package)
    mime = package / "mimetype"
    if not mime.is_file() or mime.read_bytes() != b"application/epub+zip":
        raise ValueError("EPUB has invalid or missing mimetype")
    documents = reading_order(opf, package)
    records, items = [], []
    for document_index, document in enumerate(documents):
        tree = ET.parse(document)
        rel = document.relative_to(package).as_posix()
        doc_ids = []
        for slot_index, (_, _, text) in enumerate(slots(tree)):
            segment_id = f"d{document_index:04d}-s{slot_index:05d}"
            item = {"id": segment_id, "text": text.strip(), "document": rel}
            items.append(item)
            doc_ids.append(segment_id)
        records.append({"path": rel, "ids": doc_ids})
    chunks, current, words = [], [], 0
    for item in items:
        count = max(1, word_count(item["text"]))
        if current and words + count > max_words:
            chunks.append(current); current = []; words = 0
        current.append(item); words += count
    if current:
        chunks.append(current)
    for index, chunk in enumerate(chunks, 1):
        payload = {"chunk": index, "total_chunks": len(chunks), "items": chunk}
        (chunks_dir / f"chunk-{index:04d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "source": str(source), "opf": opf.relative_to(package).as_posix(),
        "documents": records, "segments": len(items), "chunks": len(chunks),
    }
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"spine_documents": len(documents), "segments": len(items), "chunks": len(chunks), "work": str(work)}


def load_translations(directory: Path):
    result = {}
    for path in sorted(directory.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and "translations" in value:
            value = value["translations"]
        if not isinstance(value, dict):
            raise ValueError(f"Translation file is not an object: {path.name}")
        overlap = result.keys() & value.keys()
        if overlap:
            raise ValueError("Duplicate translations: " + ", ".join(sorted(overlap)[:5]))
        result.update(value)
    return result


def with_original_spacing(original, translated):
    if not isinstance(translated, str) or not translated.strip():
        raise ValueError("Empty translation")
    prefix = original[:len(original) - len(original.lstrip())]
    suffix = original[len(original.rstrip()):]
    return prefix + translated.strip() + suffix


def rebuild_navigation(package, manifest, translated):
    """Create a complete page/chapter navigation from the source spine."""
    documents = manifest["documents"]
    for nav_path in (package / "EPUB").glob("**/*"):
        if not nav_path.is_file() or nav_path.suffix.lower() not in (".xhtml", ".html"):
            continue
        tree = ET.parse(nav_path)
        nav = next((x for x in tree.getroot().iter() if local(x.tag) == "nav"), None)
        if nav is None:
            continue
        nav_type = next((value for key, value in nav.attrib.items() if local(key) == "type"), None)
        if nav_type not in ("toc", None):
            continue
        ordered = next((x for x in nav.iter() if local(x.tag) == "ol"), None)
        if ordered is None:
            body = next((x for x in tree.getroot().iter() if local(x.tag) == "body"), tree.getroot())
            ordered = ET.SubElement(body, "ol")
        ordered[:] = []
        ns = nav.tag.partition("}")[0] + "}" if "}" in nav.tag else ""
        for index, doc in enumerate(documents):
            item = ET.SubElement(ordered, ns + "li")
            href = Path(doc["path"])
            link = ET.SubElement(item, ns + "a", {"href": PurePosixPath(__import__("os").path.relpath(href, nav_path.relative_to(package).parent)).as_posix()})
            first_id = doc["ids"][0] if doc["ids"] else None
            link.text = (translated.get(first_id) or f"Section {index + 1}").strip()[:160]
        tree.write(nav_path, encoding="utf-8", xml_declaration=True)

    ncx = next(iter((package / "EPUB").glob("**/*.ncx")), None)
    if ncx and ncx.is_file():
        tree = ET.parse(ncx); root = tree.getroot()
        navmap = next((x for x in root.iter() if local(x.tag) == "navMap"), None)
        if navmap is not None:
            navmap[:] = []
            ns = navmap.tag.partition("}")[0] + "}" if "}" in navmap.tag else ""
            for index, doc in enumerate(documents, 1):
                point = ET.SubElement(navmap, ns + "navPoint", {"id": f"page_{index}", "playOrder": str(index)})
                label = ET.SubElement(point, ns + "navLabel"); ET.SubElement(label, ns + "text").text = (translated.get(doc["ids"][0]) if doc["ids"] else f"Section {index}")[:160]
                ET.SubElement(point, ns + "content", {"src": Path(doc["path"]).relative_to(ncx.relative_to(package).parent).as_posix()})
            tree.write(ncx, encoding="utf-8", xml_declaration=True)


def rebuild(work, output, language):
    work, output = Path(work).resolve(), Path(output).resolve()
    package = work / "package"
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    translated = load_translations(work / "translations")
    expected = [segment_id for doc in manifest["documents"] for segment_id in doc["ids"]]
    missing = [x for x in expected if x not in translated]
    extra = [x for x in translated if x not in set(expected)]
    if missing:
        raise ValueError(f"Missing translations: {len(missing)} (first: {missing[0]})")
    if extra:
        raise ValueError(f"Unknown translations: {len(extra)} (first: {extra[0]})")
    for doc in manifest["documents"]:
        path = package / PurePosixPath(doc["path"])
        tree = ET.parse(path)
        current_slots = list(slots(tree))
        if len(current_slots) != len(doc["ids"]):
            raise ValueError("EPUB structure changed after preparation: " + doc["path"])
        elements = list(tree.getroot().iter())
        for (element_index, field, original), segment_id in zip(current_slots, doc["ids"]):
            value = with_original_spacing(original, translated[segment_id])
            if field.startswith("attr:"):
                elements[element_index].set(field[5:], value)
            else:
                setattr(elements[element_index], field, value)
        tree.write(path, encoding="utf-8", xml_declaration=True)
    opf = package / PurePosixPath(manifest["opf"])
    opf_tree = ET.parse(opf)
    language_nodes = [x for x in opf_tree.getroot().iter() if local(x.tag) == "language"]
    if language_nodes:
        for node in language_nodes:
            node.text = language
    else:
        metadata = next((x for x in opf_tree.getroot().iter() if local(x.tag) == "metadata"), None)
        if metadata is None:
            raise ValueError("EPUB package has no metadata element")
        namespace = metadata.tag.partition("}")[0] + "}" if "}" in metadata.tag else ""
        ET.SubElement(metadata, namespace + "language").text = language
    opf_tree.write(opf, encoding="utf-8", xml_declaration=True)
    rebuild_navigation(package, manifest, translated)
    output.parent.mkdir(parents=True, exist_ok=True)
    mime = package / "mimetype"
    temp = output.with_suffix(output.suffix + ".partial")
    try:
        with zipfile.ZipFile(temp, "w", allowZip64=True) as archive:
            archive.write(mime, "mimetype", compress_type=zipfile.ZIP_STORED)
            for path in sorted(package.rglob("*")):
                if path.is_file() and path != mime:
                    archive.write(path, path.relative_to(package).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(temp) as archive:
            names = archive.namelist()
            if not names or names[0] != "mimetype" or archive.read("mimetype") != b"application/epub+zip":
                raise ValueError("Rebuilt EPUB has invalid mimetype packaging")
            if archive.testzip() is not None:
                raise ValueError("Rebuilt EPUB failed ZIP validation")
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    return {"output": str(output), "translated_segments": len(expected), "spine_documents": len(manifest["documents"])}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("source"); prep.add_argument("work"); prep.add_argument("--max-words", type=int, default=1200)
    build = sub.add_parser("rebuild")
    build.add_argument("work"); build.add_argument("output"); build.add_argument("--language", required=True)
    args = parser.parse_args()
    result = prepare(args.source, args.work, args.max_words) if args.command == "prepare" else rebuild(args.work, args.output, args.language)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

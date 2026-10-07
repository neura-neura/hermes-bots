#!/usr/bin/env python3
import os, subprocess
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

RCLONE_TIMEOUT = float(os.environ.get('OBSIDIAN_RCLONE_TIMEOUT', '30'))
VERIFY_RETRIES = 3
SCRIPT_DIR = Path(__file__).resolve().parent

class RcloneError(RuntimeError):
    pass

def rclone(*args, input=None, quick=False):
    try:
        flags = ['--contimeout', '10s', '--timeout', '45s']
        flags += ['--onedrive-delta']
        return subprocess.run(['rclone', *flags, *args], input=input, text=True, capture_output=True, timeout=RCLONE_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise RcloneError(f"rclone timed out after {RCLONE_TIMEOUT:g}s: rclone {' '.join(args)}") from e
from mcp.server.fastmcp import FastMCP

mcp = FastMCP('leo-onedrive-obsidian')
REMOTE = os.environ.get('OBSIDIAN_REMOTE', 'leo-onedrive:').strip()
VAULT_ROOT = os.environ.get('OBSIDIAN_VAULT_PATH', '').strip().strip('/')
VAULT_NAME = os.environ.get('OBSIDIAN_VAULT_NAME', '').strip() or Path(VAULT_ROOT).name

def vault_path(rel: str = '') -> str:
    if not VAULT_ROOT:
        raise RuntimeError('Falta configurar OBSIDIAN_VAULT_PATH: ruta del vault en OneDrive.')
    rel = rel.strip().strip('/')
    return VAULT_ROOT + ('/' + rel if rel else '')

def obsidian_uri(rel: str) -> str:
    if not VAULT_NAME:
        raise RuntimeError('Falta configurar OBSIDIAN_VAULT_NAME para generar enlaces Obsidian.')
    relative = rel.strip().lstrip('/')
    if relative.endswith('.md'):
        relative = relative[:-3]
    return 'obsidian://open?' + urlencode({'vault': VAULT_NAME, 'file': relative}, quote_via=quote)

def extract_created_date(content: str) -> str | None:
    lines = content.splitlines()
    if not lines or lines[0].strip() != '---':
        return None
    for line in lines[1:]:
        if line.strip() == '---':
            break
        match = re.match(r'^\s*created\s*:\s*(.*?)\s*$', line, re.IGNORECASE)
        if match:
            value = match.group(1).strip('"\'')
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                return None
    return None

def stamp_created_date(content: str, created_date: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].strip() == '---':
        end = next((index for index in range(1, len(lines)) if lines[index].strip() == '---'), None)
        if end is not None:
            found = False
            for index in range(1, end):
                if re.match(r'^\s*created\s*:', lines[index], re.IGNORECASE):
                    lines[index] = f'created: {created_date}'
                    found = True
                    break
            if not found:
                lines.insert(end, f'created: {created_date}')
            return '\n'.join(lines) + ('\n' if content.endswith('\n') else '')
    return f'---\ncreated: {created_date}\n---\n\n{content}'

def run(*args, quick=False):
    p = rclone(*args, quick=quick)
    if p.returncode:
        raise RcloneError((p.stderr or p.stdout).strip() or f'rclone exited {p.returncode}')
    return p.stdout

def safe(rel):
    rel = rel.strip().lstrip('/')
    if not rel.endswith('.md') and not rel.endswith('/'):
        rel += '.md'
    root = Path(VAULT_ROOT)
    candidate = Path(rel)
    if candidate.parts[:len(root.parts)] == root.parts:
        full = candidate
    else:
        full = root / candidate
    if not VAULT_ROOT:
        raise RuntimeError('Falta configurar OBSIDIAN_VAULT_PATH: ruta del vault en OneDrive.')
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise ValueError('La ruta debe permanecer dentro del vault de OneDrive configurado.') from exc
    if '..' in full.parts:
        raise ValueError('La ruta debe permanecer dentro del vault de OneDrive configurado.')
    return str(full)

def note_exists(path: str) -> bool:
    target = safe(path)
    # Targeted recursive listing works around OneDrive file-path lookup bugs;
    # the filename filter still enumerates only the containing folder.
    name = target.rsplit('/', 1)[-1]
    try:
        listing = run('lsf', REMOTE + target.rsplit('/', 1)[0], '--max-depth', '1', '--files-only', '--include', name, quick=True)
        return any(item.split('/')[-1] == name for item in listing.splitlines())
    except RcloneError as err:
        if 'directory not found' in str(err).casefold():
            return False
        raise

@mcp.tool()
def list_notes(folder: str = '', max_depth: int = 2) -> str:
    """Lista archivos Markdown del vault remoto de Obsidian."""
    path = vault_path(folder)
    return run('lsf', REMOTE + path, '--recursive', '--files-only', '--max-depth', str(max_depth), quick=True)

@mcp.tool()
def read_note(path: str) -> str:
    """Lee una nota Markdown remota de Obsidian."""
    target = safe(path)
    try:
        return run('cat', REMOTE + target)
    except RcloneError as first:
        # OneDrive can briefly return directory-not-found immediately after upload.
        # Confirm visibility in the parent and retry only this transient case.
        if 'directory not found' not in str(first).casefold():
            raise
        for _ in range(VERIFY_RETRIES):
            if not note_exists(target):
                continue
            try:
                return run('cat', REMOTE + target)
            except RcloneError as err:
                if 'directory not found' not in str(err).casefold():
                    raise
        raise first

@mcp.tool()
def search_notes(query: str) -> str:
    """Busca por nombre/ruta de nota, no por contenido. Para evitar duplicados al crear, usa write_note directamente (no hagas una búsqueda global)."""
    needle = query.casefold()
    listing = run('lsf', REMOTE + vault_path(), '--recursive', '--files-only', '--include', '*.md', quick=True)
    return '\n'.join(rel for rel in listing.splitlines() if needle in rel.casefold())

@mcp.tool()
def write_note(path: str, content: str, overwrite: bool = False) -> str:
    """Crea una nota Markdown; exige overwrite=True para reemplazar una existente."""
    target = safe(path)
    exists = note_exists(target)
    if exists and not overwrite:
        raise ValueError('La nota ya existe; confirma sobrescritura y usa overwrite=True.')
    today = datetime.now().astimezone().date().isoformat()
    if exists:
        previous = read_note(target)
        created_date = extract_created_date(previous) or today
    else:
        created_date = today
    content = stamp_created_date(content, created_date)
    p = rclone('rcat', REMOTE + target, input=content)
    if p.returncode: raise RcloneError((p.stderr or p.stdout).strip())
    # The write is directly to OneDrive, never to a local-only copy. Confirm that
    # OneDrive exposes the exact filename before reporting success; do not download
    # the whole note body just to verify its presence.
    for attempt in range(VERIFY_RETRIES):
        if note_exists(target):
            break
    else:
        raise RcloneError('rclone aceptó la carga, pero OneDrive aún no muestra el archivo; resultado incierto, verificar antes de reintentar.')
    relative = target[len(VAULT_ROOT.rstrip('/') + '/'):]
    return f'{target}\nbytes={len(content.encode("utf-8"))}\nsubida verificada en OneDrive\n{obsidian_uri(relative)}'

@mcp.tool()
def delete_note(path: str) -> str:
    """Borra una nota Markdown remota; requiere confirmación previa de Leo."""
    target = safe(path)
    run('deletefile', REMOTE + target)
    return target

if __name__ == '__main__': mcp.run()

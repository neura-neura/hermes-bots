#!/usr/bin/env python3
"""Local Gmail organizer. Defaults to read-only dry-run mode."""
from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


def parse_command(command: str) -> tuple[str, str]:
    """Parse the two supported Spanish commands and one sender address."""
    text = " ".join(command.strip().split())
    match = re.match(r"^(límpiame|limpiame|organízame|organizame)\s+(?:este\s+otro|este\s+email|este)\s+(\S+)$", text, re.I)
    if not match or not EMAIL_RE.fullmatch(match.group(2)):
        raise ValueError(
            "Usa: 'límpiame este email remitente@dominio.com' "
            "u 'organízame este otro remitente@dominio.com'"
        )
    verb = match.group(1).lower()
    return ("clean" if verb in {"límpiame", "limpiame"} else "organize", match.group(2))


def search_query(sender: str) -> str:
    if not EMAIL_RE.fullmatch(sender):
        raise ValueError("El remitente no parece una dirección de email válida")
    return f"from:({sender})"


def classify_message(message: dict[str, Any]) -> dict[str, Any]:
    subject = str(message.get("subject", "")).lower()
    sender = str(message.get("sender", "")).lower()
    text = f"{subject} {sender}"
    financial = any(word in text for word in ("paypal", "pago", "factura", "invoice", "payment", "receipt"))
    security = any(word in text for word in ("seguridad", "security", "login", "inicio de sesión", "alerta"))
    newsletter = any(word in text for word in ("newsletter", "boletín", "unsubscribe", "darse de baja", "promoción", "oferta"))
    academic = any(word in text for word in ("universidad", "ieu", "actividad", "evaluación", "entrega"))
    if financial or security:
        return {"label": "Importante", "archive": False, "reason": "Parece relacionado con dinero o seguridad."}
    if academic:
        return {"label": "Académico", "archive": False, "reason": "Parece relacionado con estudios o entregas."}
    if newsletter:
        return {"label": "Newsletters", "archive": True, "reason": "Parece una promoción o boletín."}
    return {"label": "Por revisar", "archive": False, "reason": "No hay suficiente señal para archivarlo automáticamente."}


def _header(headers: list[dict[str, str]], name: str) -> str:
    wanted = name.lower()
    return next((h.get("value", "") for h in headers if h.get("name", "").lower() == wanted), "")


def _decode_body(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _message_summary(raw: dict[str, Any]) -> dict[str, Any]:
    headers = raw.get("payload", {}).get("headers", [])
    return {
        "id": raw.get("id", ""),
        "threadId": raw.get("threadId", ""),
        "sender": _header(headers, "From"),
        "subject": _header(headers, "Subject"),
        "date": _header(headers, "Date"),
        "snippet": raw.get("snippet", ""),
        "labelIds": raw.get("labelIds", []),
    }


@dataclass
class GmailClient:
    service: Any

    @classmethod
    def authenticate(cls, client_secret: str, token_path: str) -> "GmailClient":
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError("Faltan dependencias. Ejecuta: python -m pip install -r requirements.txt") from exc

        token = Path(token_path).expanduser()
        creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(os.path.expanduser(client_secret), SCOPES)
            creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
            token.parent.mkdir(parents=True, exist_ok=True)
            token.write_text(creds.to_json(), encoding="utf-8")
            try:
                token.chmod(0o600)
            except OSError:
                pass
        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False))

    def search(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        response = self.service.users().messages().list(userId="me", q=query, maxResults=min(limit, 100)).execute()
        result = []
        for item in response.get("messages", []):
            raw = self.service.users().messages().get(userId="me", id=item["id"], format="full").execute()
            result.append(_message_summary(raw))
        return result

    def labels(self) -> dict[str, str]:
        rows = self.service.users().labels().list(userId="me").execute().get("labels", [])
        return {row["name"]: row["id"] for row in rows}

    def ensure_label(self, name: str) -> str:
        existing = self.labels()
        if name in existing:
            return existing[name]
        created = self.service.users().labels().create(
            userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        ).execute()
        return created["id"]

    def apply(self, message_id: str, label_name: str, archive: bool) -> dict[str, Any]:
        label_id = self.ensure_label(label_name)
        body: dict[str, list[str]] = {"addLabelIds": [label_id]}
        if archive:
            body["removeLabelIds"] = ["INBOX"]
        return self.service.users().messages().modify(userId="me", id=message_id, body=body).execute()


def print_plan(mode: str, messages: list[dict[str, Any]]) -> list[tuple[str, str, bool]]:
    plan = []
    if not messages:
        print("No encontré mensajes de ese remitente.")
        return plan
    print(f"Encontré {len(messages)} mensaje(s). Propuestas (sin cambios):")
    for msg in messages:
        decision = classify_message(msg)
        action = "archivar y etiquetar" if mode == "clean" and decision["archive"] else "etiquetar"
        if mode == "clean" and not decision["archive"]:
            action = "no archivar; revisar"
        print(f"- {msg['subject'] or '(sin asunto)'} | {msg['date']} | {action} → {decision['label']}")
        print(f"  {decision['reason']}")
        if mode == "organize" or decision["archive"]:
            plan.append((msg["id"], decision["label"], decision["archive"] and mode == "clean"))
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Organizador local y conservador de Gmail")
    parser.add_argument("command", nargs="?", help="Comando en español entre comillas")
    parser.add_argument("--client-secret", default=os.environ.get("GMAIL_CLIENT_SECRET", "~/Downloads/client_secret.json"))
    parser.add_argument("--token", default=os.environ.get("GMAIL_TOKEN", "~/.gmail-organizer/token.json"))
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--apply", action="store_true", help="Aplicar las propuestas después de confirmar")
    args = parser.parse_args(argv)
    if not args.command:
        parser.error("falta el comando; usa --help")
    try:
        mode, sender = parse_command(args.command)
        client = GmailClient.authenticate(args.client_secret, args.token)
        plan = print_plan(mode, client.search(search_query(sender), args.limit))
        if not args.apply or not plan:
            print("Modo simulación: no se modificó Gmail.")
            return 0
        answer = input("¿Aplicar estas propuestas? Escribe SI para confirmar: ").strip()
        if answer != "SI":
            print("Cancelado; no se modificó Gmail.")
            return 0
        for message_id, label, archive in plan:
            client.apply(message_id, label, archive)
        print(f"Aplicadas {len(plan)} propuesta(s).")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

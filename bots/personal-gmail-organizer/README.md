# Organizador local de Gmail

Bot local y conservador para organizar mensajes de Gmail desde comandos en español.

## Seguridad por defecto

- Sin `--apply`, solo lee y muestra propuestas.
- No envía correo ni elimina mensajes.
- `límpiame` solo archiva automáticamente candidatos detectados como newsletters/promociones.
- Mensajes financieros, de seguridad y académicos nunca se archivan automáticamente.
- Para cambiar Gmail hay que usar `--apply` y escribir `SI` en la confirmación.
- El token OAuth se guarda localmente en `~/.gmail-organizer/token.json` con permisos restringidos.

## Instalación en macOS

```bash
cd ~/gmail-organizer-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Configurar Gmail API (una sola vez)

1. Abre https://console.cloud.google.com/projectselector2/home/dashboard
2. Crea o selecciona un proyecto.
3. En https://console.cloud.google.com/apis/library activa **Gmail API**.
4. En https://console.cloud.google.com/apis/credentials crea credenciales:
   **Create Credentials → OAuth client ID → Desktop app**.
5. Si Google muestra la pantalla de audiencia, añade tu cuenta como **Test user**:
   https://console.cloud.google.com/auth/audience
6. Descarga el JSON y colócalo, por ejemplo, en:
   `~/Downloads/gmail-client-secret.json`

No pegues el contenido del JSON, contraseñas ni códigos en el chat.

## Uso

Activar el entorno virtual en cada terminal nueva:

```bash
cd ~/gmail-organizer-bot
source .venv/bin/activate
```

Primera ejecución: se abrirá Thorium (o el navegador predeterminado) para autorizar Gmail.

```bash
python gmail_organizer.py \
  --client-secret ~/Downloads/gmail-client-secret.json \
  "organízame este otro remitente@example.com"
```

Limpiar un remitente en modo simulación:

```bash
python gmail_organizer.py \
  --client-secret ~/Downloads/gmail-client-secret.json \
  "límpiame este email newsletters@example.com"
```

Aplicar las propuestas después de revisarlas:

```bash
python gmail_organizer.py \
  --client-secret ~/Downloads/gmail-client-secret.json \
  --apply \
  "organízame este otro remitente@example.com"
```

El bot aceptará también las formas sin acento: `limpiame` y `organizame`.

## Qué hace cada comando

- `organízame este otro remitente@example.com`: busca mensajes de ese remitente y propone una etiqueta (`Importante`, `Académico`, `Newsletters` o `Por revisar`).
- `límpiame este email remitente@example.com`: hace lo anterior y propone archivar únicamente newsletters/promociones.
- `--limit 50`: cambia el máximo de mensajes inspeccionados.
- Sin `--apply`: siempre es simulación.

La clasificación inicial usa reglas locales y no envía el contenido de tus mensajes a ningún modelo externo. Más adelante se puede añadir un modelo local para mejorarla.

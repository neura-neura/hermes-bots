# Bot de impresiones: flujo rápido

**Uso diario: `/nuevo` → enviar contenido → `/cerrar`.** Consulta [GUIA_RAPIDA.md](GUIA_RAPIDA.md) para instrucciones de uso.

## Servidor

Datos en `data/pedidos.sqlite3`, entorno en `.env` (modo 600). No hay puertos públicos ni servidor web; usa long polling. Python 3.13 en Docker, SQLite WAL, un solo consumidor por token/base.

```sh
cd bots/impresiones
docker compose ps
docker compose logs --tail=30 bot
docker compose build
docker compose up -d
```

Para instalar desde cero: copia `.env.example` a `.env`, configura token de BotFather, grupo, usuarios autorizados y administradores, construye la imagen y ejecuta `docker compose run --rm bot python -m impresion_bot init`. Después `docker compose up -d`. Para instalación local Python 3.11+: crea `.venv`, instala `requirements.lock` y `pip install --no-deps -e .`; carga el entorno y ejecuta `python -m impresion_bot run`.

El bot debe recibir todos los mensajes del grupo: administrador (situación actual) o privacidad desactivada en BotFather con retirada/reincorporación al grupo. Para limpiar mensajes humanos necesita **Borrar mensajes**. No necesita administrar temas ni usuarios. Solo admite acciones de los dos usuarios configurados en el grupo configurado. Los demás miembros del grupo pueden leer los pedidos.

## Primera configuración

Desde un usuario en `ADMIN_IDS`, asocia cada tema ejecutando dentro de él `/configurar nuevos`, `/configurar listos`, `/configurar proceso`, `/configurar falta`, `/configurar armar` y `/configurar terminados`. También admite ID explícito y nombres personalizados. No descubre temas por sus títulos. Las asociaciones existentes se conservan al actualizar.

El menú `/` se registra automáticamente para Magui y Kevin. `/configurar` solo se muestra a los administradores de la app. Los permisos siempre se validan también en código.

## Captura libre

`/nuevo [nombre opcional]` abre un registro persistente en Nuevos. Se incluyen únicamente los mensajes de **quien abrió la captura**, en ese tema, hasta `/cerrar`. Ambas personas pueden capturar a la vez sin mezclar sus mensajes. Mensajes fuera de una captura se ignoran, salvo respuestas/vinculaciones explícitas. Los comandos no cuentan como contenido. Una captura persiste tras reinicios, hasta cerrarla; no tiene caducidad silenciosa. Si olvidas cerrarla, tus siguientes mensajes normales en ese tema seguirán dentro del pedido.

Se guardan texto, leyendas, entidades y metadatos de archivos, mensajes originales y IDs de las copias actuales. No se descargan los bytes. Se admiten texto, documentos, fotos, vídeo, audio, voz, animaciones, stickers, videonotas, contactos y ubicación. Contenido protegido se rechaza; encuestas/dados/medios pagados se avisan como no soportados. Un pedido vacío no se puede cerrar. Cancelar no elimina una captura persistente: `/cerrar` conserva lo recibido.

`/cerrar` termina la captura, no la entrega. La ficha compacta muestra ID, título, etapa y cantidad de mensajes/archivos. El primer texto/leyenda sirve como título si no se indicó nombre. **Añadir más** reabre captura para ese pedido en su tema actual; termina nuevamente con `/cerrar`.

El formulario antiguo sigue disponible con `/formulario [plantilla]`; no es necesario usarlo. Pedidos anteriores conservan su interfaz y datos. [Referencia del flujo anterior](docs/REFERENCIA_ANTERIOR.md) documenta ese modo opcional; sus afirmaciones sobre no copiar ni borrar solo describen la versión anterior.

## Traslados y limpieza

Botones de avance: Nuevos → Listos → Proceso → Armar → Terminados. **Falta algo** permite bloquear desde una etapa abierta sin pedir un formulario; **Resuelto** vuelve a Armar, según el flujo solicitado. Desde Falta también se admite `/pasar ID etapa-anterior`. El texto que falte se puede agregar mediante Añadir más. No se exige fecha, responsable o motivo en el modo rápido.

`/pasar ID [2|3|4|5|6]` hace lo mismo que los botones. Al pulsar avanzar:

1. Se persiste un traslado con la lista exacta de mensajes actuales; no se cambia todavía la etapa.
2. Se llama `copyMessages` por mensaje/álbum, conservando el orden. Cada operación depende de la anterior. Se comprueba que el número de IDs devueltos coincide con lo solicitado.
3. Solo al confirmarse **todas** las copias se actualizan los IDs actuales, etapa e historial y se publica la nueva ficha.
4. Se ofrece **Limpiar tema anterior**. Un segundo botón confirma expresamente el borrado de los mensajes enumerados. No se incluyen mensajes ajenos ni respuestas de otras personas fuera del pedido. Los comandos de apertura/cierre/paso registrados sí se incluyen. La limpieza confirmada incluye la ficha anterior, los avisos del bot asociados al pedido y los comandos de control registrados. Los mensajes fijados de configuración y los de otros pedidos se conservan.
5. Cada borrado tiene reintentos y auditoría. Telegram puede rechazar mensajes antiguos o sin permisos: se avisa y no se oculta el fallo.

No hay traslado literal de un hilo en la Bot API. Las copias no conservan necesariamente las relaciones de respuesta ni atribución original; actor/contenido original siguen en la base. Nunca se borra automáticamente al cambiar de etapa. No cambies ni borres manualmente el contenido mientras el bot lo está trasladando. Las copias actuales son las fuentes para el siguiente traslado; no depende de originales ya limpiados.

## Fallos y garantías

- Updates y callbacks repetidos se deduplican en SQLite. Captura, contenido, eventos y outbox se confirman en una transacción. IDs de pedido no se reutilizan.
- Capturas abiertas y traslados en curso bloquean otros traslados del mismo pedido. Botones viejos se rechazan por versión. No se modifican originales al recibir texto libre.
- Un timeout de `sendMessage` o `copyMessages`, o un reinicio durante su envío, queda **incierto**. No se reenvía automáticamente. Copias incompletas también quedan inciertas: no se puede deducir qué mensaje omitió Telegram solo a partir de un resultado más corto.
- En esos casos no se cambia la etapa ni se ofrece borrar. `/configurar fallos` muestra las operaciones. Revisa el destino antes de usar el reintento confirmado: puede crear copias duplicadas si el envío anterior sí llegó. No hay garantía exactly-once entre SQLite y Telegram. Las copias parciales se conservan, nunca se eliminan a ciegas.
- `/configurar recuperar JOB MENSAJE` permite vincular una ficha de texto enviada con resultado incierto si coincide exactamente. Para copias de contenido, revisa y usa reintento explícito; esta versión no permite reconciliar manualmente un álbum parcial. Si desapareció la fuente o no se puede copiar, hay que recuperar ese contenido antes de completar el traslado.
- `RetryAfter` y operaciones idempotentes tienen esperas y hasta cinco intentos. Un borrado repetido de mensaje ya ausente cuenta como resuelto. Errores se auditan sin tokens ni URLs secretas. Las publicaciones anteriores pueden quedar visibles si Telegram rechaza editar/borrar.

Las tablas `captures`, `content`, `transfers`, `transfer_items` y `dependencies` amplían idempotentemente el esquema v1 sin borrar datos previos. `album_buffer` conserva metadatos temporales de álbumes para respuestas fuera de captura, con TTL de asociación de diez minutos y máximo 500 elementos. Eventos son inmutables mediante triggers SQLite; no sustituye control de acceso al servidor. La fuente de verdad es SQLite.

## Copias de seguridad

Antes de actualizar y periódicamente:

```sh
cd bots/impresiones
docker compose stop bot
docker compose run --rm bot python -m impresion_bot backup /app/data/respaldo-NOMBRE-NUEVO.sqlite3
docker compose up -d
```

El comando usa backup de SQLite y rechaza sobrescribir. Copia el resultado a otro disco seguro. Para restaurar: detén el bot, conserva base y WAL/SHM actuales en otra carpeta, coloca el backup como base sin WAL/SHM antiguos, ajusta permisos y ejecuta `check` antes de iniciar. Una copia antigua puede retroceder IDs/offset y perder pedidos: reconcilia antes de aceptar altas. No ejecutes dos consumidores con el mismo token ni base.

Los textos, teléfonos escritos libremente y metadatos son datos privados del negocio; no se cifran dentro de SQLite. Restringe base/backups, no incluyas tarjetas ni secretos en pedidos. Limpiar Telegram **no borra el registro central**. Los bytes de archivos no están respaldados. Los enlaces privados requieren pertenecer al grupo.

## Fuentes oficiales y límites

Verificado el 1 de octubre de 2026:

- [copyMessages](https://core.telegram.org/bots/api#copymessages): 1–100 IDs ascendentes, conserva álbumes, puede omitir elementos no copiables. No transporta literalmente un hilo.
- [deleteMessage](https://core.telegram.org/bots/api#deletemessage): permiso de borrado y restricciones, normalmente menos de 48 horas. No es posible prometer limpieza completa de trabajos viejos.
- [Privacidad](https://core.telegram.org/bots/features#privacy-mode): un administrador recibe mensajes del grupo; con privacidad desactivada hay que volver a añadirlo para aplicar el cambio.
- [Updates](https://core.telegram.org/bots/api#getting-updates): Telegram retiene actualizaciones hasta 24 horas. Mensajes nunca recibidos durante una interrupción prolongada no se pueden recuperar del historial arbitrariamente.
- [Archivos](https://core.telegram.org/bots/api#sending-files): `file_id` pertenece al bot; reutilización sin descargar. [getFile](https://core.telegram.org/bots/api#getfile): descarga estándar hasta 20 MB, sin uso en este flujo.
- [python-telegram-bot](https://docs.python-telegram-bot.org/en/stable/telegram.bot.html): cliente 22.8, `copy_messages`, `delete_message`, `send_message` y polling serial propio para confirmar offsets después del commit.

## Pruebas

```sh
PTB_TIMEDELTA=1 .venv/bin/pytest -q
```

Incluyen SQLite real, cliente PTB con transporte simulado, captura libre/reinicio, aislamiento entre personas/temas, álbumes, avance completo 1→2→3→5→4→5→6, duplicados, timeout, copia parcial sin borrado y limpieza confirmada fallida. No se generan pedidos reales durante el despliegue. La prueba de uso real corresponde a un pedido enviado por ustedes.

### Limpieza completa y ubicación de la ficha

Se registran las publicaciones del bot con su pedido y Topic (`bot_posts`), comandos explícitos (`control_posts`) y publicaciones pendientes incluidas en la limpieza (`cleanup_bot_jobs`). Los mensajes que llegan tarde también se borran si ya fueron incluidos en una limpieza confirmada. Se reconstruyen asociaciones de avisos anteriores únicamente cuando sus IDs, botones o claves de operación identifican el pedido; no se adivinan comandos humanos antiguos ni se borra el resto del historial del grupo. `transfer_sources` conserva el Topic original aunque se cambie después la configuración.

Puedes volver a usar **Limpiar tema anterior** de un traslado ya limpiado para retirar los avisos antiguos identificados. Si el pedido regresó al tema de origen, se rechaza la limpieza antigua para conservar la ficha y contenido vigentes. Al completar la limpieza no se deja una ficha residual en el origen. Telegram sigue pudiendo rechazar mensajes antiguos.

El botón de En proceso es **Listos / Armar → 5**; desde Armar aparecen **Entregado → 6** y **Falta algo → 4**. Las acciones desde un Topic distinto al estado vigente se rechazan; consulta `/pedido ID` en el Topic actual. Ambos usuarios tienen permitido 3→5; el modo rápido no exige un rol para avanzar.

Pruebas completas en el servidor (el `/tmp` limitado del contenedor de producción no está dimensionado para crear decenas de bases de prueba):

```sh
docker run --rm --tmpfs /tmp:size=128m -e PTB_TIMEDELTA=1 -v ./tests:/app/tests:ro impresionestg-bot:local python -m pytest -q -p no:cacheprovider /app/tests
```

## Progreso, acceso al destino y limpieza automática

Cada traslado muestra un único aviso editable en el origen con el número real de mensajes copiados, porcentaje y botón **Abrir fase de destino**. Después de copiar todo indica que está publicando la ficha; solo confirma que el pedido llegó cuando Telegram confirma esa ficha. El botón abre directamente el Topic configurado (requiere pertenecer al grupo); también aparece en la ficha y en la oferta de limpieza. Los enlaces siguen la [documentación oficial de enlaces de Topics](https://core.telegram.org/api/links#forum-topic-links). Los avisos se actualizan mediante [editMessageText](https://core.telegram.org/bots/api#editmessagetext), no enviando una notificación nueva por archivo. Se priorizan los avisos y se conserva el ritmo de peticiones; puede haber unos segundos entre avances y Telegram puede pedir esperar. El progreso cuenta mensajes confirmados por lote/álbum, no bytes ni elementos individuales de un álbum durante una petición.

La limpieza manual usa otro aviso editable en el destino: `2/8 mensajes · 25%` hasta **Tema anterior limpio**. El aviso del traslado en el origen también se retira cuando se limpia. No se barre el Topic entero: solo mensajes asociados a ese pedido.

La limpieza automática empieza **desactivada**. Un administrador de `ADMIN_IDS` puede usar:

- `/autolimpiar` — consultar el estado.
- `/autolimpiar activar` — autorizar persistentemente la limpieza automática para los próximos traslados de ambos operadores.
- `/autolimpiar desactivar` — volver al borrado confirmado manualmente.

La preferencia se guarda en SQLite (`meta.auto_cleanup`) y sobrevive a reinicios y despliegues. Cada traslado conserva el modo elegido al iniciarse en `transfer_ui`; cambiarlo mientras copia afecta a los siguientes traslados. En modo automático, después de confirmar **todas las copias y la ficha nueva**, se eliminan los mensajes, comandos y avisos del pedido del Topic de origen sin ofrecer ni enviar avisos de limpieza. El aviso de copia sí muestra progreso mientras trabaja. Las copias del destino y otros pedidos se conservan. Si Telegram rechaza el borrado, se conserva el mensaje rechazado y se avisa una sola vez por operación; la copia queda guardada. No se puede asegurar el borrado de mensajes de más de 48 horas. El historial audita la activación, desactivación y borrados; el modo automático sustituye la confirmación individual por esa autorización explícita persistente. No limpia traslados anteriores por activarlo.

Las vistas de progreso usan la misma cola persistente, con un único edit pendiente que se actualiza al estado más reciente. Un fallo del aviso no bloquea las copias. Si el envío inicial tiene resultado incierto no se repite a ciegas; el traslado sigue y el fallo queda registrado. El progreso tampoco garantiza una animación continua durante una petición de red: avanza cuando Telegram confirma cada lote.

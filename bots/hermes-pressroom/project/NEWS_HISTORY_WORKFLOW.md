# Historial persistente de noticias — The K Times

Esta política es permanente y no depende del chat. Cargar `profiles/the-k-times/config.yaml` y este documento en cada ejecución. No cambiar cron desde los scripts. No usar `build_r2/r3/r4/r5.py` para el periódico diario.

## Almacenamiento y estados

`profiles/<publication-id>/news-history.sqlite3` es el historial durable, fuera de cache. SQLite usa transacciones y bloqueo de escritura (`BEGIN IMMEDIATE`) para volver a comprobar el historial justo antes de publicar. La identidad de publicación se verifica dentro de la base. No se crea una base vacía al faltar el archivo: detenerse y recuperar la base, no olvidar el historial. Respaldarlo junto con el perfil; no aplicarle limpieza de temporales.

Cada registro conserva fecha/revisión/noticia, event_id editorial estable, resumen del evento, titular, texto original de síntesis y SHA-256 normalizado, todas las fuentes con URL original y canónica, título, `published_at` de la fuente (null si desconocido), `retrieved_at` con zona y evidencia adicional. `recorded_at` es la hora de incorporación al historial, no la hora de publicación de la fuente.

- **consulted**: guardar inmediatamente cada lote consultado, incluso si no se utiliza. Observaciones distintas se conservan, no sobrescriben la investigación anterior.
- **selected**: el renderizador registra las noticias cuya revisión editorial pasó. Un render fallido puede dejar consultas/selecciones, pero nunca publicaciones.
- **published**: solamente `finalize`, después del PDF archivado, verificación independiente, densidad y revisión visual aprobadas y vinculadas por hashes. Significa publicado como PDF validado, no impreso físicamente. Registrar/consultar o renderizar NO publica.

Para una edición manual reintentada después de un fallo, inspecciona el estado durable y archivo antes de comenzar; si la ejecución previa está `unknown`, no reuses su trabajo ni repitas a ciegas acciones de archivo/impresión. Si no hay artefacto final, crea una revisión y directorio nuevos, vuelve a investigar y sigue todas las puertas. No lances el comando de cron desde un proceso interactivo de corta duración para trabajo largo: el propietario puede desaparecer y dejar estado `unknown`. Mantén el gateway host ejecutándose, y el owner que dispara debe tener duración suficiente; sigue `cron runs` hasta estado terminal. Si la llamada CLI puede expirar, dispara el job de forma desacoplada con un mecanismo persistente y comprueba owner/estado después; nunca interpretes un timeout como cancelación segura.

La cola correcta es `Samsung_debian_ZeroTier`; `Samsung @ debian (ZeroTier)` es su nombre visible/de perfil. Resolver el valor guardado contra `printers.json` por `display_name` y enviar a `queue` (o consultar CUPS por esa cola), no pasar el display_name a lpoptions/lp. Antes de imprimir, verificar con `lpstat -p Samsung_debian_ZeroTier -l` y `lpoptions -p Samsung_debian_ZeroTier`; si la cola acepta trabajos y coincide con el nombre visible, continuar con ella sin tratar la diferencia de nombres como discrepancia ni bloquear la edición. Registrar ambos nombres para trazabilidad.

**Recuperación editorial obligatoria:** no reportar fallo por insuficiencia de la primera tanda, objetivo de páginas no alcanzado, falta de una imagen concreta o defectos reparables de render/densidad. La propia especificación declara flexible el objetivo de 7–10 páginas; una edición más corta y verificada es preferible a detenerse o rellenar. Amplía consultas y fuentes siguiendo la escalera editorial persistente; abre fuentes originales y primarias, busca imágenes pertinentes adicionales, reequilibra páginas, columnas y saltos, reduce páginas o dimensiona imágenes de nuevo. Un índice grande no requiere cargar miles de líneas: usa history-index compacto y pagina con `--offset/--limit`, manteniendo verificados `count` y `digest`; la puerta requiere cobertura completa, no pegar el archivo entero en contexto. Consultas no publicadas no son publicaciones ni quedan vetadas: registra los nuevos lotes, verifica historia y elige eventos distintos. Tras cada arreglo vuelve a renderizar y ejecutar QA afectado. Solo termina con fallo cuando exista bloqueo concreto no solventable tras probar estas alternativas y se informe del error real; jamás simules QA, fuentes, comparación o publicación.




Desde `.`:

1. Resolver `FECHA` con `publication.timezone`, cierre y revisión. Comprobar si ya existe la edición del día; no duplicar el cron ni imprimir de nuevo sin orden manual.
2. Obtener un índice compacto que representa todos los artículos publicados de los demás días:

```sh
.venv/bin/python scripts/news_history.py history-index --publication the-k-times --date FECHA --output DIRECTORIO_TRABAJO/history-snapshot.json
```

El comando acepta `--offset N --limit N` para recuperar páginas pequeñas del índice; el `count` y `digest` siguen describiendo el historial completo.

El índice enumera **todas** las referencias publicadas, event_id, resumen factual, titular, huella del texto y URLs canónicas. Incluye `count` y un `digest` SHA-256 calculado sobre los registros canónicos completos; por tanto, el índice compacto conserva cobertura total sin pasar a contexto payloads extensos repetidos. Verifica count/digest antes de investigar. Si alguna noticia candidata puede coincidir semánticamente, recupera solo esa referencia completa desde SQLite para comparar evidencia concreta. Nunca cargues miles de líneas ni filtres el índice a ayer, URLs, ventanas temporales o primeros resultados. Para revisar consultas no utilizadas, leer la tabla `records` con `status='consulted'` mediante SQLite/Python. No están prohibidas por haber sido consultadas.

3. Investigar noticias actuales. Después de **cada lote**, persistir un JSON de investigación con el mismo esquema de procedencia que una edición (`publication_id`, `date`, `revision`, `source_records`, `stories`; para `paragraphs` pueden usarse notas factuales propias). Reutilizar identidades de eventos del historial, aunque cambie el medio, idioma, titular o URL. Incluir también candidatos descartados:

```sh
.venv/bin/python scripts/news_history.py consult LOTE_INVESTIGADO.json
```

No hace falta `history_review` para guardar consultas. Las fuentes no abiertas deben identificarse honestamente como tales en campos de evidencia; no inventar tiempos ni hechos. Persistir lotes incluso si luego falla la selección. No concluyas que faltan noticias por una búsqueda inicial escasa: amplía por etapas a reportes fiables en otros idiomas, fuentes originales, temas adyacentes y noticias generales recientes; traduce al español y registra la procedencia. Si aún conviene, incorpora contexto o material evergreen claramente diferenciado y respaldado. Completa el cotejo y la mejor edición útil dentro de las restricciones; solo detén por un bloqueo real. Nunca rellenes con duplicados ni afirmaciones sin fuente.

4. Seleccionar y comparar editorialmente **cada noticia candidata con cada referencia del índice**. Es una comparación de hechos, participantes, fechas y acontecimientos, no una comparación literal de URLs. Si una noticia cubre el mismo evento que varias notas anteriores, conservar todas sus referencias. Consolidar cobertura de varios medios del mismo evento en una sola noticia de la nueva edición. No inventar IDs nuevos para eludir coincidencias. Registrar comparaciones específicas, no copiar una declaración genérica de novedad. Para una posible coincidencia, recuperar el/los registro(s) completo(s) concretos desde SQLite antes de decidir `same_event`/`different_event`.
5. Crear `EDITION_JSON` con el esquema siguiente y ejecutar:

```sh
.venv/bin/python scripts/news_history.py check EDITION_JSON
.venv/bin/python scripts/render_newspaper.py EDITION_JSON --output-dir NEWDIR
.venv/bin/python scripts/verify_edition.py NEWDIR
```

`NEWDIR` debe ser un directorio nuevo en `newspapers/the-k-times/YYYY/MM/DD/rN`. La puerta de historial está integrada al inicio del renderizador y del verificador: no puede omitirse simplemente saltando el comando `check`. Si falta la base, la procedencia, el digest vigente o alguna comparación, detener publicación e impresión. Reinvestigar/revisar si otra edición se publicó durante la ejecución. `check` es de solo lectura; el renderizador también registra `selected`.

6. Inspeccionar todas las páginas y recortes inferiores. Escribir `visual-review.json` con estado `PASS` o `PASS_WITH_NOTED_WHITESPACE`, hash del PDF y, para cada página, `page`, `reviewed: true`, e `images` con `path` y `sha256` de las imágenes realmente revisadas. Nunca inventar esa aprobación. Preservar `density-check.json` generado por el renderizador y `independent-qa.json` producido por el verificador.
7. Archivar `sources.json` **idéntico a `edition.json.source_records`**, `production.log` no vacío y `manifest.json`: `publication_id`, `edition_date`, `edition_revision`, `pdf_filename`, `pdf_hash`, `story_count`, `page_count`, `qa_status: PASS`, `errors: []`, `status: ARCHIVED` (o `COMPLETE_PDF_ONLY`). Incluir también los demás campos habituales del manifest. El PDF es canónico; no cambiar el modelo después del render sin regenerar y revisar.
8. Publicar transaccionalmente y comprobar lectura antes de cualquier impresión:

```sh
.venv/bin/python scripts/news_history.py finalize NEWDIR --output NEWDIR/history-publication.json
.venv/bin/python scripts/news_history.py stats --publication the-k-times
```

Si `finalize` falla, **no imprimir ni anunciar edición terminada**. Conservar el borrador para reparar. `finalize` vuelve a verificar el snapshot dentro de la transacción; solo registra la publicación cuando todos los comprobantes coinciden. Una revisión ya publicada es inmutable. Reejecutar el mismo `finalize` sin cambios es idempotente. Después de la publicación, seguir el flujo de impresión separado configurado, sin confundir aceptación de cola con impresión física. Cambios posteriores a campos de impresión del manifest no alteran el PDF publicado; no volver a finalizar una revisión con manifest modificado.

**Ejecuciones de varios días:** tras finalizar cada edición, leer de vuelta `stats` y generar un `history-index` nuevo para la fecha siguiente. Añadir y comparar cada referencia recién publicada; nunca reutilizar un digest previo. Si el digest queda obsoleto, construir una revisión nueva y preservar la anterior como borrador no publicado.

**Cierre de impresión:** registrar el job ID, las opciones y la lectura real de CUPS. Aceptación, lista de trabajos completados o cola vacía son evidencia de estado de la cola, no de entrega física al usuario. Si el usuario confirma que recibió las páginas y salieron satisfactoriamente, esa confirmación sí cuenta como verificación independiente: actualizar `manifest.json` a `print_status: PRINT_COMPLETED` y `status: COMPLETE`, añadir quién/cuándo confirmó y actualizar `production.log`. No seguir llamando al trabajo “no verificado” después de esa confirmación. Sin confirmación del usuario, no afirmar que las hojas llegaron.

## Esquema adicional del JSON de edición

Los campos existentes de maquetación permanecen. Añadir:

```json
{
  "publication_id": "the-k-times",
  "source_records": [
    {
      "id": 1,
      "url": "https://example.org/noticia",
      "title": "Título de la fuente realmente consultada",
      "published_at": null,
      "retrieved_at": "2026-09-22T05:20:00-06:00",
      "evidence_file": "ruta de la evidencia real si existe"
    }
  ],
  "history_review": {
    "snapshot": "digest obtenido de history-snapshot.json",
    "reviewer": "Hermes Pressroom — revisión editorial del turno"
  },
  "stories": [
    {
      "id": "identificador-local",
      "event_id": "identidad-estable-del-acontecimiento",
      "event_summary": "Qué sucedió, quién, dónde y cuándo; no solo el tema general",
      "headline": "Titular",
      "paragraphs": ["Texto original investigado"],
      "sources": [1],
      "history_comparisons": [
        {
          "prior_ref": "2026-09-21/r5/grandblue",
          "relationship": "different_event",
          "rationale": "Explicar las diferencias factuales concretas con aquella noticia"
        }
      ]
    }
  ]
}
```

`history_comparisons` debe contener **una entrada por cada ref del snapshot**, sin omisiones ni repeticiones. `relationship`: `same_event` o `different_event`. Un ID de evento coincidente, URL canónica compartida, texto idéntico o evaluación `same_event` bloquea la repetición entre días, salvo actualización válida. La normalización de URLs solo elimina fragmentos, barra final y tracking conocido; no pretende resolver sindicación semántica.

## Actualizaciones y revisiones

Una actualización factual sustancial debe conservar el event_id de la historia seguida. Añadir al artículo:

```json
{
  "label": "ACTUALIZACIÓN · TEMA",
  "update": {
    "prior_refs": ["2026-09-21/r5/grandblue"],
    "new_facts": ["Hecho nuevo concreto, comprobado en las fuentes de esta edición"],
    "rationale": "Por qué este hecho cambia sustancialmente lo publicado antes"
  }
}
```

Las referencias deben cubrir todas las coincidencias y aparecer **literalmente en el texto de los párrafos o deck** para que el lector vea el vínculo. No es actualización cambiar de medio, traducir, reescribir o añadir relleno. Un texto idéntico sigue bloqueado aunque se añada metadata de actualización. URLs genéricas compartidas (por ejemplo kernel.org) producen una coincidencia conservadora: documentar la actualización real o usar evidencia específica del nuevo evento; no declarar falsamente una actualización.

Las revisiones manuales del mismo día se excluyen del snapshot de otros días y pueden reutilizar noticias. Deben usar una revisión nueva y otro directorio. No se pueden sobrescribir revisiones publicadas. Las noticias publicadas en revisiones anteriores de otros días siguen protegiendo frente a repetición: no se borran al revisar.

## Backfill y límites honestos

Migración ejecutada: `scripts/backfill_news_history.py`, limitada al r5 aceptado del 21-09-2026 y al hash `f7166d086f018046c5ab247a385669802b10afdd5d7403d46104ff40e91dc92f`. Comprueba manifest, PDF, QA, densidad, hashes de revisión visual y las 23 evidencias. No altera el archivo histórico ni reconstruye PDF.

- 22 artículos publicados; 23 fuentes únicas; 20 identidades de evento. `ai/oversight` y `gnome/papers` son pares de noticia/recuadro del mismo acontecimiento, conservados como realmente aparecieron. No se duplicaron r1–r4.
- Los timestamps de recuperación son los originales. Los 23 timestamps de publicación de fuentes faltan en `sources.json`: se conserva `published_at: null` y las fechas narrativas originales, sin adivinarlas de las URLs.
- El backfill no convierte toda la investigación antigua no utilizada en eventos inventados. El registro incremental de **todas las consultas futuras** es obligatorio.
- No hay un clasificador semántico automático infalible: el motor exige comparaciones completas y rechaza coincidencias explícitas/deterministas. La calidad de `event_id`, decisiones `different_event` y hechos nuevos depende del juicio editorial respaldado por fuentes. Una declaración falsa puede engañar una validación estructural; no afirmar que URL/SHA resuelven esa limitación.
- El archivado final depende de QA independiente y aprobación visual auténticas; las pruebas sintéticas del gate no sustituyen esas revisiones.

## Verificación y recuperación

```sh
.venv/bin/python -W ignore::DeprecationWarning -m unittest discover -s tests -v
.venv/bin/python scripts/news_history.py stats --publication the-k-times
```

La supresión de warnings solo evita avisos Swig de PyMuPDF ya presentes en la dependencia. Las pruebas usan bases temporales aisladas y una lectura del backfill real; no publican noticias de prueba en producción. Para nuevas publicaciones, inicializar explícitamente su propia base mediante `Ledger.initialize` tras comprobar sus archivos; no copiar el historial de The K Times. No borrar/reinicializar una base existente para superar una puerta de control.

## Instrucción para el cron 9bbfeb848509 (agente padre)

Añadir al job: «Carga el perfil persistente y `NEWS_HISTORY_WORKFLOW.md`. Antes de seleccionar, genera y lee el snapshot completo para la fecha local; registra cada lote de noticias consultadas mediante `news_history.py consult`, incluidas las no elegidas. El JSON debe contener procedencia y comparaciones editoriales completas contra el historial. No repetir eventos publicados otros días; admitir solamente actualizaciones sustanciales etiquetadas, con referencias y hechos nuevos justificados. Ejecuta renderizador y verificador reutilizables; tras la revisión visual y manifest/sources/log archivados, exige `news_history.py finalize NEWDIR` y lectura de `stats` antes de imprimir. Si falla cualquier puerta o falta la base, no imprimas; informa del bloqueo. No reconstruyas revisiones históricas ni reinicialices la base.»

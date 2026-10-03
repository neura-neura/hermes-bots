# Abrir → mandar todo → cerrar

## Un pedido nuevo

1. En el tema **1 Nuevos / Por confirmar**, escribe `/nuevo` (opcional: `/nuevo Ana`).
2. Envía lo que tengas: textos, fotos, PDF, Word, archivos, uno por mensaje o varios juntos. No necesitas responder a la ficha ni rellenar campos.
3. Escribe `/cerrar` o pulsa **Cerrar captura**. Aparece la ficha del pedido.

**Importante:** mientras tienes un pedido abierto, todos tus mensajes normales en ese tema pertenecen a ese pedido. Los mensajes de la otra persona y de otros temas no se mezclan. Cada persona puede tener una captura abierta. No cambia automáticamente al pedido siguiente: escribe `/nuevo` otra vez.

`/cerrar` solo significa “ya terminé de mandar cosas”; no significa entregar al cliente.

## Pasarlo a la siguiente etapa

| Quién / momento | Botón en la ficha |
|---|---|
| Magui confirma el pedido | ✅ Confirmado → 2 |
| Kevin empieza a hacerlo | 🖨️ Empezar → 3 |
| Kevin termina de imprimir / acabar | 📦 Listos / Armar → 5 |
| Falta cualquier cosa | 🟡 Falta algo → 4 |
| Se resolvió lo que faltaba | 📦 Resuelto → 5 |
| Magui entrega | ✅ Entregado → 6 |

El bot copia **todos los mensajes y archivos** y publica la ficha en el destino. Espera a que aparezca allí antes de seguir. Los álbumes se conservan cuando Telegram los admite.

Después pulsa **🧹 Limpiar tema anterior → Sí, limpiar tema anterior**. Solo entonces borra los mensajes del pedido en el tema anterior. Nunca borra mensajes ajenos al pedido. También se retiran la ficha anterior y los avisos del bot de ese pedido. Los mensajes fijados del negocio y otros pedidos permanecen.

Si Telegram rechaza un borrado, por ejemplo porque pasaron 48 horas, el bot avisa y conserva lo que no pudo borrar. No elimina nada si faltan copias por confirmar.

## Ejemplo de Ana

1. Magui: `/nuevo Ana`.
2. Magui manda “20 invitaciones a color”, dos PDF y tres fotos. Termina con `/cerrar`.
3. Magui pulsa **Confirmado → 2**. Cuando llega todo, limpia el tema anterior.
4. Kevin pulsa **Empezar → 3**; después **Listos / Armar → 5**. Puede limpiar cada tema anterior al terminar el traslado.
5. Magui revisa y entrega con **Entregado → 6**. Si falta algo, usa **Falta algo → 4**; al resolverlo, **Resuelto → 5**.

Para agregar más cosas después: en la ficha actual pulsa **📎 Añadir más**, envía lo que falte y termina con `/cerrar`.

También puedes usar `/pasar T-2026-0001 3` para copiar ese pedido al tema 3. Sin número de etapa, `/pasar T-2026-0001` usa la siguiente etapa normal. Es más cómodo usar los botones.

Si ya habías limpiado antes de esta mejora, puedes pulsar otra vez **Limpiar tema anterior** del traslado y confirmar para retirar los avisos antiguos que el bot identifica.

## Ver el avance y automatizar la limpieza

1. Pulsa el botón para pasar el pedido. Verás **Copiando… 1/5 mensajes** en un aviso que se actualiza.
2. Pulsa **➡️ Abrir fase de destino** para ir directamente al nuevo tema.
3. Si limpias manualmente, verás el contador de mensajes retirados hasta **Tema anterior limpio**.

Para saltarte la confirmación de limpieza en todos los próximos traslados, Kevin escribe **`/autolimpiar activar`** una sola vez. Se guarda incluso si el bot se reinicia. El bot limpia automáticamente al confirmar que llegó todo y la ficha nueva, sin avisos de limpieza. Si algo falla, sí avisa y conserva lo que no pudo borrar.

Para apagarlo: **`/autolimpiar desactivar`**. Para consultar cómo está: **`/autolimpiar`**. Al instalar esta mejora permanece desactivado.

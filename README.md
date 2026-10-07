# Hermes Bots

Una colección de bots y proyectos creados para [Hermes Agent](https://github.com/NousResearch/hermes-agent). Cada carpeta contiene las instrucciones reutilizables y, cuando existe, el código y la guía de instalación.

## Bots y proyectos

| Bot/proyecto | Descripción | Contenido |
|---|---|---|
| [hermes-bot-creator](bots/hermes-bot-creator/) | Crea, inspecciona, modifica, duplica y administra bots nativos de Hermes. | Servidor MCP, instrucciones y guía |
| [lia-email](bots/lia-email/) | Organiza Gmail y Outlook. | Instrucciones del bot |
| [little-k-companion](bots/little-k-companion/) | Compañero virtual con Telegram, traducción y voz. | Instrucciones, aplicación y guía |
| [anna-translator](bots/anna-translator/) | Asistente de traducción editorial multilingüe. | Instrucciones del bot |
| [leo-notes](bots/leo-notes/) | Organiza calendario, tareas y notas. | Instrucciones y servidor MCP de OneDrive/Obsidian |
| [joe-x](bots/joe-x/) | Asistente editorial y operador de publicaciones en X. | Instrucciones del bot |
| [Hermes Pressroom](bots/hermes-pressroom/) | Producción, renderizado y archivo de periódicos personalizados. | Instrucciones y herramientas reutilizables de composición |
| [mia-reddit](bots/mia-reddit/) | Asistente para Reddit. | Instrucciones del bot |
| [Hermes Telegram Bot](bots/hermes-telegram-bot/) | Puente de Telegram a Hermes con manejo de medios y notas. | Aplicación, bridge web y guía |
| [Impresiones](bots/impresiones/) | Flujo de pedidos de impresión en Telegram. | Aplicación, despliegue y guía rápida |
| [Personal Gmail Organizer](bots/personal-gmail-organizer/) | Automatiza la organización de Gmail. | Script, instrucciones y pruebas |

## Perfiles personales

Los archivos `SOUL.md` describen el comportamiento de cada bot. Son plantillas reutilizables y pueden mencionar capacidades, rutas o servicios que requieren adaptación local. No son exportaciones completas de perfiles Hermes.

## Privacidad

Este repositorio no incluye tokens, credenciales OAuth, archivos `.env`, sesiones, bases de datos, historiales de conversación, memorias personales, configuraciones locales ni ediciones de periódicos. Configura secretos mediante variables de entorno o el almacén local correspondiente. Revisa cada `.env.example` y README antes de desplegar un bot.

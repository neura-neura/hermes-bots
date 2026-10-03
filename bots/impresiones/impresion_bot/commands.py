"""Menú de Telegram por usuario; los permisos siguen validándose en Engine."""
from telegram import BotCommand, BotCommandScopeChatMember

COMMANDS = [
    ('nuevo', 'Abrir pedido: manda todo sin llenar campos'),
    ('cerrar', 'Cerrar captura y guardar pedido'),
    ('pasar', 'Pasar pedido: ID y etapa 2, 3, 4, 5 o 6'),
    ('pedido', 'Ver un pedido: escribe su ID'),
    ('buscar', 'Buscar por cliente, ID o trabajo'),
    ('pendientes', 'Ver trabajos con pendientes'),
    ('hoy', 'Ver entregas de hoy y vencidas'),
    ('resumen', 'Ver el resumen del negocio'),
    ('vincular', 'Vincular un archivo: responde al original e indica ID'),
    ('cancelar', 'Cancelar la captura actual'),
    ('ayuda', 'Ver ayuda y botones'),
]


async def register_commands(bot, config):
    for uid in (config.magui, config.kevin):
        commands = [BotCommand(*item) for item in COMMANDS]
        if uid in config.admins:
            commands.append(BotCommand('configurar', 'Configurar temas y revisar fallos'))
            commands.append(BotCommand('autolimpiar', 'Activar o desactivar limpieza automática'))
        scope = BotCommandScopeChatMember(config.group, uid)
        # Fallback para cualquier idioma de la app y variante española explícita.
        for language in ('', 'es'):
            await bot.set_my_commands(commands, scope=scope, language_code=language)

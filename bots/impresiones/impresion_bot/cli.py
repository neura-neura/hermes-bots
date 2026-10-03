import argparse
import asyncio
import fcntl
import logging
import os
from pathlib import Path
from telegram import Bot
from telegram.error import NetworkError, RetryAfter
from .config import Config
from .store import Store
from .engine import Engine
from .delivery import Delivery
from .commands import register_commands


async def run(config, store):
    engine = Engine(store,config)
    async with Bot(config.token) as bot:
        info = await bot.get_webhook_info()
        if info.url:
            raise ValueError('Existe un webhook activo. Elimínalo explícitamente antes de usar polling.')
        me = await bot.get_me()
        engine.username = me.username
        chat = await bot.get_chat(config.group)
        if not chat.is_forum:
            raise ValueError('El grupo configurado debe ser un supergrupo con Topics.')
        await register_commands(bot, config)
        worker = Delivery(store,config,bot)
        worker.recover()
        backoff = 1
        while True:
            engine.reminder()
            # Ráfagas pequeñas y ritmo conservador: menos de 20 mensajes/minuto/grupo.
            working=False
            for _ in range(4):
                if not await worker.once():
                    break
                working=True
                if worker.last_kind!='ack':
                    await asyncio.sleep(3.1)
            offset = store.one("SELECT value FROM meta WHERE key='offset'")
            try:
                updates = await bot.get_updates(offset=int(offset['value']) if offset else None,
                    timeout=0 if working else 5,read_timeout=15,allowed_updates=['message','callback_query'])
                backoff=1
                for update in updates:
                    engine.process(update.to_dict())
                    # Solo confirmar el Update a Telegram después del commit del dominio y outbox.
                    with store.db:
                        store.db.execute("INSERT INTO meta VALUES('offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(update.update_id+1),))
            except RetryAfter as error:
                delay=error.retry_after
                await asyncio.sleep(delay.total_seconds() if hasattr(delay,'total_seconds') else delay)
            except NetworkError:
                logging.warning('Red no disponible al recibir actualizaciones; reintentando.')
                await asyncio.sleep(backoff)
                backoff=min(60,backoff*2)


def main():
    parser=argparse.ArgumentParser(description='Bot de pedidos de impresión')
    parser.add_argument('action',choices=['run','init','backup','check','demo','discover'],nargs='?',default='run')
    parser.add_argument('target',nargs='?')
    args=parser.parse_args()
    os.umask(0o077)
    # httpx puede registrar URLs con el token; no habilitar sus logs.
    logging.basicConfig(level=logging.WARNING,format='%(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    logging.getLogger('httpcore').setLevel(logging.CRITICAL)
    if args.action=='demo':
        from .demo import demo
        demo(); return
    if args.action=='discover':
        asyncio.run(discover(os.environ['BOT_TOKEN']))
        return
    config=Config.env()
    Path(config.database).parent.mkdir(parents=True,exist_ok=True)
    with open(config.database+'.lock','a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Ya hay otro proceso usando esta base de datos.') from None
        store=Store(config.database)
        if args.action=='init':
            Engine(store,config)
            print('Base inicializada. Asocia los seis Topics con /configurar.')
        elif args.action=='backup':
            if not args.target: parser.error('Indica un archivo nuevo de destino.')
            store.backup(args.target)
            print('Copia consistente creada.')
        elif args.action=='check':
            print('Integridad:',store.one('PRAGMA integrity_check')[0])
            print('Operaciones por estado:',[tuple(r) for r in store.all('SELECT status,count(*) FROM outbox GROUP BY status')])
        else:
            try:
                asyncio.run(run(config,store))
            except KeyboardInterrupt:
                pass
            except Exception as error:
                # Evita imprimir URLs/tokens contenidos en excepciones de transporte.
                logging.error('Bot detenido (%s). Revisa configuración, permisos y conectividad.',type(error).__name__)
                raise SystemExit(1) from None


async def discover(token):
    """Solo muestra IDs de comandos explícitos de identificación, sin guardar contenidos."""
    async with Bot(token) as bot:
        if (await bot.get_webhook_info()).url:
            raise ValueError('Desactiva el webhook antes de descubrir IDs.')
        me = await bot.get_me()
        print(f'Envía /identificar@{me.username} desde cada usuario dentro del Topic. Esperando hasta 30 s…')
        updates = await bot.get_updates(timeout=30,read_timeout=40,allowed_updates=['message'])
        found=False
        for update in updates:
            msg=update.message
            if msg and msg.text and msg.text.split()[0].lower() in {'/identificar',f'/identificar@{me.username.lower()}'}:
                print(f'GROUP_ID={msg.chat_id} USER_ID={msg.from_user.id if msg.from_user else "anónimo"} TOPIC_ID={msg.message_thread_id}')
                found=True
        if not found:
            print('No llegó un comando de identificación en el lote. Envíalo y repite; este modo no confirma ni descarta Updates.')

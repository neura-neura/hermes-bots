"""Demostración aislada sin token, red ni datos reales."""
from .config import Config
from .store import Store
from .engine import Engine
from .ui import card


def demo():
    config=Config('no-usado',-1001234567890,111,222,frozenset({111}))
    store=Store(':memory:')
    engine=Engine(store,config)
    with store.db:
        for index, stage in enumerate(('nuevos','listos','proceso','falta','armar','terminados'),10):
            store.db.execute('UPDATE stages SET thread_id=? WHERE key=?',(index,stage))
        order=engine.d.create(111,engine.d.parse('cliente=Cliente ficticio\ntipo=impresión\ndescripcion=20 invitaciones\ncantidad=20\npago=parcial'))
        order=engine.d.move(order['id'],order['version'],111,'listos')
        order=engine.d.assign(order['id'],order['version'],222,'produccion')
        order=engine.d.move(order['id'],order['version'],222,'proceso')
        order=engine.d.pending(order['id'],order['version'],222,'Confirmar tamaño','Magui')
        order=engine.d.move(order['id'],order['version'],222,'falta')
        order=engine.d.resolve(order['id'],order['version'],111,1)
        order=engine.d.move(order['id'],order['version'],111,'proceso')
        order=engine.d.move(order['id'],order['version'],222,'armar')
        order=engine.d.move(order['id'],order['version'],111,'terminados')
        print(Engine._plain_html(card(order,engine.d)))
        print('\nEventos auditables:',store.one('SELECT count(*) FROM events')[0])

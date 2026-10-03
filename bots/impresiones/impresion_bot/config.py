import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    token: str
    group: int
    magui: int
    kevin: int
    admins: frozenset[int]
    timezone: str = 'America/Monterrey'
    database: str = 'data/pedidos.sqlite3'
    reminder_time: str = ''
    templates_path: str = ''

    def __post_init__(self):
        ZoneInfo(self.timezone)
        if self.group >= 0 or self.magui <= 0 or self.kevin <= 0 or self.magui == self.kevin:
            raise ValueError('Configura un grupo negativo y dos usuarios positivos diferentes.')
        if not self.admins or not self.admins <= {self.magui, self.kevin}:
            raise ValueError('ADMIN_IDS debe contener responsables autorizados.')
        if self.reminder_time:
            from datetime import time
            time.fromisoformat(self.reminder_time)
            if len(self.reminder_time) != 5:
                raise ValueError('REMINDER_TIME debe ser HH:MM.')

    @classmethod
    def env(cls):
        return cls(os.environ['BOT_TOKEN'], int(os.environ['GROUP_ID']),
                   int(os.environ['MAGUI_ID']), int(os.environ['KEVIN_ID']),
                   frozenset(int(x) for x in os.environ['ADMIN_IDS'].split(',')),
                   os.getenv('TIMEZONE', 'America/Monterrey'), os.getenv('DATABASE', 'data/pedidos.sqlite3'),
                   os.getenv('REMINDER_TIME', ''), os.getenv('TEMPLATES_PATH', ''))

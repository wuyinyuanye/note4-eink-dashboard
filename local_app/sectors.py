"""Fixed sector proxies and complete, current quote snapshots."""
from datetime import datetime
from pathlib import Path
from market import Market, startup_quotes_current, session

# Existing Zectrix dashboard watchlist: sector names describe the proxy, not an official sector index.
SECTORS = (('科创芯片','sh588200'), ('算力芯片','sz159819'), ('人工智能','sz399971'),
           ('PCB板','sz159732'), ('5G通信','sz399994'), ('风电设备','sz159855'),
           ('稀有金属','sz159608'), ('有色金属','sz159871'), ('光伏设备','sz399808'),
           ('新能源电池','sz399976'), ('半导体','sz399995'), ('稀土永磁','sz159830'),
           ('红利指数','sh000015'), ('电子板块','sz399986'))
CODES = [code for _, code in SECTORS]

class Sectors(Market):
    def __init__(self, directory):
        path = Path(directory)/'sectors'; path.mkdir(parents=True, exist_ok=True)
        super().__init__(path)
        if self.codes != CODES:
            self.codes = CODES.copy(); self.rows = []; self.fetched_at = None
        self.interval = 300
        if len(self.rows) != len(CODES) or {row['code'] for row in self.rows} != set(CODES):
            self.rows = []; self.fetched_at = None

    def accept(self, rows, now, error=None):
        if not error and (not rows or len(rows) != len(CODES) or not startup_quotes_current(rows,CODES,now)
                          or not all(row['traded'] for row in rows)
                          or (session(now)[1] and any((now-datetime.fromisoformat(row['stamp'])).total_seconds()>600 for row in rows))):
            error = '板块报价不完整或不是最近交易日，保留上次画面'
        super().accept(rows,now,error)
        return self.error is None

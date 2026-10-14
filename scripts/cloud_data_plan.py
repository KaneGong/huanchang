"""Read-only migration inventory. Does not send data or reveal private fields."""
import argparse,json,sqlite3
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('work/huanchang'));a=p.parse_args()
db=sqlite3.connect('file:'+str((a.data/'huanchang.sqlite3').resolve())+'?mode=ro',uri=True)
if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('Source integrity failed')
counts={t:db.execute('SELECT count(*) FROM "'+t+'"').fetchone()[0] for t in ['users','works','comments','messages','uploads','drafts']}
missing=sum(not (a.data/'media'/row[0]).is_file() for row in db.execute('SELECT id FROM uploads'))
print(json.dumps({'mode':'read-only plan, no transfer','counts':counts,'missingMedia':missing,'decisionsNeeded':['new project and region','fresh launch versus importing local test users','media and account transfer scope','clear sessions and email challenges']},ensure_ascii=False,indent=2))
db.close()

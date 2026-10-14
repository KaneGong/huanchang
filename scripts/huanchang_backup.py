#!/usr/bin/env python3
"""Consistent local backup; session tokens are not copied into the saved database."""
import argparse
import datetime
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=ROOT/'work/huanchang');p.add_argument('--out',type=Path,default=ROOT/'work/huanchang-backups');a=p.parse_args()
source=a.data/'huanchang.sqlite3'
if not source.is_file():raise SystemExit('找不到幻场数据，请核对目录。')
target=a.out/datetime.datetime.now().strftime('%Y%m%d-%H%M%S');target.mkdir(parents=True,exist_ok=False,mode=0o700)
with closing(sqlite3.connect(source)) as src,closing(sqlite3.connect(target/'huanchang.sqlite3')) as dest:
    src.backup(dest)
    dest.execute('DELETE FROM sessions')
    if dest.execute("SELECT 1 FROM sqlite_master WHERE name='email_challenges'").fetchone():dest.execute('DELETE FROM email_challenges')
    dest.commit()
    if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('备份数据库检查失败。')
if (a.data/'media').is_dir():shutil.copytree(a.data/'media',target/'media')
for file in target.rglob('*'):
    file.chmod(0o700 if file.is_dir() else 0o600)
print('已完成本地备份：'+str(target))

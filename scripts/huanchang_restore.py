#!/usr/bin/env python3
"""Restore into a NEW data directory; never overwrites an existing site."""
import argparse
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

def restore(source,target):
    source=source.resolve();target=target.resolve()
    if target.exists():raise ValueError('恢复目标必须是尚不存在的新目录，原站点不会被覆盖。')
    database=source/'huanchang.sqlite3'
    if not database.is_file():raise ValueError('备份缺少数据库。')
    with closing(sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('备份完整性检查失败。')
        for fid,size in db.execute('SELECT id,size FROM uploads'):
            file=source/'media'/fid
            if not file.is_file() or file.stat().st_size!=size:raise ValueError('备份有图片缺失或大小异常，请使用完整备份。')
    target.parent.mkdir(parents=True,exist_ok=True)
    stage=target.with_name(target.name+'.restoring')
    if stage.exists():raise ValueError('发现未完成的恢复目录，请人工核对后再继续。')
    try:
        stage.mkdir(mode=0o700);shutil.copy2(database,stage/'huanchang.sqlite3')
        if (source/'media').is_dir():shutil.copytree(source/'media',stage/'media')
        else:(stage/'media').mkdir()
        with closing(sqlite3.connect(stage/'huanchang.sqlite3')) as db:
            db.execute('DELETE FROM sessions')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='email_challenges'").fetchone():db.execute('DELETE FROM email_challenges')
            db.commit()
        for file in stage.rglob('*'):file.chmod(0o700 if file.is_dir() else 0o600)
        stage.rename(target)
    except Exception:
        shutil.rmtree(stage);raise
    return target

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--backup',required=True,type=Path);p.add_argument('--target',required=True,type=Path);a=p.parse_args()
    try:print('已恢复到新目录：'+str(restore(a.backup,a.target)))
    except (ValueError,sqlite3.Error,OSError) as e:raise SystemExit('恢复未完成：'+str(e))

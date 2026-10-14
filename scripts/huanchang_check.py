#!/usr/bin/env python3
"""Read-only service, mail failure, disk and backup freshness check."""
import argparse,datetime,json,shutil,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8790');p.add_argument('--data',type=Path,default=Path('work/huanchang'));p.add_argument('--backups',type=Path,default=Path('work/huanchang-backups'));p.add_argument('--max-backup-hours',type=int,default=24);a=p.parse_args();problems=[]
try:
    with urllib.request.urlopen(a.url.rstrip('/')+'/healthz',timeout=5) as r:health=json.load(r)
    if not health.get('ok'):problems.append('站点健康检查未通过。')
    if health.get('mail',{}).get('failures',0):problems.append('本次启动以来出现邮件投递失败，需核对邮件服务。')
except Exception:problems.append('无法连接站点健康检查。')
if not a.data.is_dir():problems.append('数据目录不存在。')
else:
    free=shutil.disk_usage(a.data).free
    if free<1024**3:problems.append('可用磁盘不足1GB，请检查图片和备份空间。')
backups=list(a.backups.glob('*/huanchang.sqlite3'))
if not backups:problems.append('没有找到备份。')
elif datetime.datetime.now().timestamp()-max(f.stat().st_mtime for f in backups)>a.max_backup_hours*3600:problems.append('最近备份已超过设置时限。')
for item in problems:print(item)
if not problems:print('服务响应、邮件失败记录、磁盘余量及备份时效检查通过。')
raise SystemExit(1 if problems else 0)

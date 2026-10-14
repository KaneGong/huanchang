"""Optional labeled examples only. Never imports local users or their uploads."""
import argparse,json,os
from pathlib import Path

def seed_examples(db):
    import huanchang_server as hc
    seed=json.loads((hc.WEB/'seed.json').read_text())
    for uid,profile in seed['users'].items():
        existing=db.execute('SELECT demo FROM users WHERE id=?',(uid,)).fetchone()
        if existing and not existing['demo']:raise ValueError('Example id is already in use')
        profile.update(available=False,creatorListed=False,avatar='',banner='',featured=[],deliverables=[],revision=1)
        db.execute('INSERT INTO users(id,handle,data,demo) VALUES(?,?,?,1) ON CONFLICT(id) DO NOTHING',(uid,uid,hc.dump(profile)))
    for i,work in enumerate(seed['works']):
        work.update(sample=True,files=[],url='',role='演示素材与内容编排',process='',tools='',lookingFor='',rights=hc.RIGHTS[0],curatedReason='')
        db.execute('INSERT INTO works(id,author,data,created,updated) VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING',(work['id'],work['author'],hc.dump(work),hc.now()-i*60000,hc.now()))
    return len(seed['works'])

def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args()
    if not a.apply:print('Plan only: add labeled bundled examples; no local user data or uploads. Use --apply only for the chosen cloud project.');return
    from cloud.database import connect
    url=os.environ.get('HUANCHANG_DATABASE_URL','')
    if not url:raise SystemExit('Private cloud database configuration required')
    with connect(url) as db:
        db.execute('BEGIN IMMEDIATE');total=seed_examples(db)
    print('Labeled examples prepared:',total,'posts; demo login and private contact remain disabled.')

if __name__=='__main__':main()

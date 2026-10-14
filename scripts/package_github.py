"""Make a clean, reviewed release tree, never a recursive workspace upload."""
from pathlib import Path
import json,shutil
root=Path(__file__).resolve().parents[1];out=root/'outputs/huanchang-github-release'
out.mkdir(exist_ok=True)
files=['huanchang_server.py','hc_readiness.py','cloud_app.py','requirements.txt','pyproject.toml','vercel.json','.gitignore','.vercelignore','CONTRIBUTING.md','SECURITY.md','RIGHTS.md']
for name in files:shutil.copy2(root/name,out/name)
for directory in ['cloud','scripts']:
    shutil.copytree(root/directory,out/directory,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copytree(root/'outputs/huanchang-v1',out/'outputs/huanchang-v1',dirs_exist_ok=True,ignore=shutil.ignore_patterns('qa','__pycache__','*.pyc'))
for name in ['test_huanchang.py','test_readiness.py','test_cloud.py']:
    (out/'tests').mkdir(exist_ok=True);shutil.copy2(root/'tests'/name,out/'tests'/name)
(out/'tests/__init__.py').write_text('')
for name in ['README.md','VERCEL-SUPABASE.md','.env.cloud.example','.env.example','Dockerfile','compose.yaml','Caddyfile']:
    source=root/'deployment'/name
    if source.exists():(out/'deployment').mkdir(exist_ok=True);shutil.copy2(source,out/'deployment'/name)
shutil.copytree(root/'docs/github/assets',out/'docs/github/assets',dirs_exist_ok=True)
for name in ['README.zh-CN.md','README.en.md','METADATA.md']:
    source=root/'docs/github'/name
    if source.exists():shutil.copy2(source,out/'docs/github'/name)
shutil.copy2(root/'docs/github/README.zh-CN.md',out/'README.md');shutil.copy2(root/'docs/github/README.en.md',out/'README.en.md')
shutil.copy2(root/'docs/LOCALIZATION.md',out/'docs/LOCALIZATION.md')
workflow=root/'.github/workflows/check.yml'
if workflow.exists():(out/'.github/workflows').mkdir(parents=True,exist_ok=True);shutil.copy2(workflow,out/'.github/workflows/check.yml')
paths=[str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()]
for path in paths:
    if path.startswith(('work/','public/')) or path.endswith(('.sqlite3','.db','.pyc')) or (Path(path).name.startswith('.env') and not path.endswith('.example')):raise RuntimeError('Private file in release')
(root/'work/cloud-release-2026-10-14/package-manifest.json').write_text(json.dumps(paths,ensure_ascii=False,indent=2))
print(f'Release tree prepared: {len(paths)} files; no local account database or uploaded media.')

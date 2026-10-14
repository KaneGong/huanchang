"""Build only the approved frontend; seed, QA and local data stay private."""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parents[1]
source=root/'outputs/huanchang-v1';target=root/'public'
target.mkdir(exist_ok=True)
for path in source.iterdir():
    if path.name in ['assets','vendor'] or path.suffix in ['.mjs','.css','.svg','.html']:
        destination=target/path.name
        if path.is_dir():shutil.copytree(path,destination,dirs_exist_ok=True)
        else:shutil.copy2(path,destination)
# Never let a previously staged seed or QA directory survive an incremental build.
for name in ['seed.json','qa','README.md']:
    path=target/name
    if path.is_dir():shutil.rmtree(path)
    elif path.exists():path.unlink()
print('Approved frontend prepared; local user data and seed are excluded.')

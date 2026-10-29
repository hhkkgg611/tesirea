"""Build the source + CPython 3.13 Windows wheel distribution from an allowlist."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parent
TOP='Hypit-Studio-Windows'
FILES=['index.html','style.css','app.js','remix.css','remix.js','README.md',
       'START-HERE-WINDOWS.txt','THIRD-PARTY-WINDOWS.txt','Setup.bat','Start.bat',
       'windows.py','windows-ffmpeg.json','requirements.txt','requirements.lock',
       'requirements-windows.lock','setup.sh','start.sh','.gitignore','package_windows.py']


def build(wheels,output):
    wheels=Path(wheels);output=Path(output)
    paths=[ROOT/name for name in FILES]
    paths+=sorted((ROOT/'backend').glob('*.py'))+sorted((ROOT/'tests').glob('test_*.py'))
    records={str(path.relative_to(ROOT)).replace('\\','/'):path.read_bytes() for path in paths}
    wheel_paths=sorted(wheels.glob('*.whl'))
    if len(wheel_paths)<24:raise ValueError('The complete Windows dependency wheel set is required.')
    hashes={}
    for path in wheel_paths:
        if 'manylinux' in path.name or 'linux_' in path.name or 'macosx' in path.name:raise ValueError('Non-Windows wheel: '+path.name)
        if '-cp' in path.name and '-cp313-' not in path.name:raise ValueError('Wrong Python ABI: '+path.name)
        data=path.read_bytes();hashes[path.name]=hashlib.sha256(data).hexdigest()
        records['vendor/wheels/'+path.name]=data
    records['vendor/wheels-sha256.json']=(json.dumps(hashes,indent=2)+'\n').encode()
    records['SHA256SUMS.txt']=''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in sorted(records.items())).encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,data in sorted(records.items()):archive.writestr(TOP+'/'+name,data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:raise ValueError('ZIP CRC validation failed.')
    sha=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(sha+'  '+output.name+'\n',encoding='ascii')
    return {'zip':str(output),'bytes':output.stat().st_size,'files':len(records),'wheels':len(hashes),'sha256':sha}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheels',required=True,type=Path)
    parser.add_argument('--output',default=ROOT/'dist'/'Hypit-Studio-Windows.zip',type=Path)
    args=parser.parse_args()
    print(json.dumps(build(args.wheels,args.output)))

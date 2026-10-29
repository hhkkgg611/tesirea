"""Windows setup/launcher. Uses only the standard library until setup is complete."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import urllib.request
import venv
import webbrowser
import zipfile

ROOT=Path(__file__).resolve().parent
VENV=ROOT/'.venv'
PYTHON=VENV/'Scripts'/'python.exe'
TOOLS=ROOT/'.tools'/'ffmpeg'
WHEELS=ROOT/'vendor'/'wheels'


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def check_python():
    if sys.platform!='win32' or sysconfig.get_platform()!='win-amd64':
        raise RuntimeError('This launcher needs Windows x64 and 64-bit Python 3.13. See START-HERE-WINDOWS.txt.')
    if sys.version_info[:2]!=(3,13):
        raise RuntimeError('Install 64-bit Python 3.13 from python.org, then run Setup.bat. Bundled wheels target Python 3.13.')


def check_wheels():
    manifest=ROOT/'vendor'/'wheels-sha256.json'
    if not manifest.is_file():raise RuntimeError('Bundled dependencies are missing. Extract the entire ZIP before running Setup.bat.')
    records=json.loads(manifest.read_text(encoding='utf-8'))
    if not records:raise RuntimeError('The dependency manifest is empty.')
    for name,expected in records.items():
        if Path(name).name!=name or not name.endswith('.whl'):raise RuntimeError('Invalid dependency manifest.')
        path=WHEELS/name
        if not path.is_file() or digest(path)!=expected:
            raise RuntimeError('Dependency checksum failed: '+name+'. Download and extract a fresh copy of the app ZIP.')


def extract_ffmpeg(archive, destination):
    """Extract only named executables/licenses, never arbitrary archive paths."""
    destination=Path(destination);(destination/'bin').mkdir(parents=True,exist_ok=True)
    wanted={'ffmpeg.exe':None,'ffprobe.exe':None}
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            parts=PurePosixPath(item.filename).parts
            if '..' in parts or PurePosixPath(item.filename).is_absolute():
                raise RuntimeError('Unsafe path in FFmpeg archive.')
            if len(parts)>=2 and parts[-2]=='bin' and parts[-1] in wanted:
                if wanted[parts[-1]] is not None:raise RuntimeError('Duplicate executable in FFmpeg archive.')
                wanted[parts[-1]]=item
        if not all(wanted.values()):raise RuntimeError('FFmpeg archive does not contain both ffmpeg.exe and ffprobe.exe.')
        for name,item in wanted.items():
            with z.open(item) as source,(destination/'bin'/name).open('wb') as out:shutil.copyfileobj(source,out)
        for item in z.infolist():
            name=PurePosixPath(item.filename).name
            if not item.is_dir() and name.upper() in {'LICENSE','LICENSE.TXT','README.TXT'}:
                (destination/name).write_bytes(z.read(item))


def tools_environment():
    env=os.environ.copy()
    if (TOOLS/'bin'/'ffmpeg.exe').is_file() and (TOOLS/'bin'/'ffprobe.exe').is_file():
        env['PATH']=str(TOOLS/'bin')+os.pathsep+env.get('PATH','')
    return env


def valid_tools(env):
    for tool in ('ffmpeg','ffprobe'):
        executable=shutil.which(tool,path=env.get('PATH',''))
        if not executable:return False
        try:
            result=subprocess.run([executable,'-version'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,env=env)
        except (OSError,subprocess.TimeoutExpired):return False
        if result.returncode:return False
    return True


def install_tools():
    if valid_tools(tools_environment()):
        print('FFmpeg and ffprobe are available.')
        return
    manifest=json.loads((ROOT/'windows-ffmpeg.json').read_text(encoding='utf-8'))
    if not manifest['url'].startswith('https://github.com/GyanD/codexffmpeg/releases/download/'):
        raise RuntimeError('Unexpected FFmpeg download source.')
    print('Downloading FFmpeg '+manifest['version']+' from GyanD on GitHub (first setup only)...',flush=True)
    TOOLS.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='download-',dir=TOOLS.parent) as folder:
        archive=Path(folder)/'ffmpeg.zip'
        request=urllib.request.Request(manifest['url'],headers={'User-Agent':'Hypit-Studio-Windows-Setup'})
        with urllib.request.urlopen(request,timeout=90) as response,archive.open('wb') as out:
            total=0
            while chunk:=response.read(1024*1024):
                total+=len(chunk)
                if total>250*1024*1024:raise RuntimeError('FFmpeg archive exceeds the download limit.')
                out.write(chunk)
        print('Verifying FFmpeg SHA-256...',flush=True)
        if digest(archive)!=manifest['sha256']:
            raise RuntimeError('FFmpeg checksum mismatch. Installation stopped; the downloaded binaries were not used.')
        staged=Path(folder)/'ffmpeg'
        extract_ffmpeg(archive,staged)
        # This directory belongs to this installer; preserve unrelated existing tools.
        if TOOLS.exists():raise RuntimeError('A partial .tools/ffmpeg folder already exists. Rename it and run Setup.bat again.')
        shutil.move(str(staged),str(TOOLS))
    if not valid_tools(tools_environment()):raise RuntimeError('Windows could not run the downloaded media tools. Check START-HERE-WINDOWS.txt.')
    print('FFmpeg installed in this app folder. System PATH was not changed.')


def setup():
    check_python()
    check_wheels()
    if not PYTHON.is_file():
        print('Creating the local Python environment...',flush=True)
        venv.EnvBuilder(with_pip=True).create(VENV)
    print('Installing the bundled Windows dependencies...',flush=True)
    subprocess.run([str(PYTHON),'-m','pip','install','--disable-pip-version-check','--no-index',
        '--find-links',str(WHEELS),'--only-binary=:all:','-r',str(ROOT/'requirements-windows.lock')],check=True,cwd=ROOT)
    subprocess.run([str(PYTHON),'-m','pip','check'],check=True,cwd=ROOT)
    install_tools()
    print('\nSetup complete. Double-click Start.bat. No API key is required during setup.')


def studio_health(port):
    # Direct loopback health check only; never bypass the proxy for provider traffic.
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=1)
    try:
        connection.request('GET','/api/health')
        response=connection.getresponse()
        body=response.read(128*1024)
        value=json.loads(body)
        return response.status==200 and value.get('ok') is True and 'runware' in value and 'wavespeed' in value
    except (OSError,ValueError,http.client.HTTPException):return False
    finally:connection.close()


def open_when_ready(port,process):
    for _ in range(120):
        if process.poll() is not None:return
        if studio_health(port):
            webbrowser.open('http://127.0.0.1:'+str(port))
            return
        time.sleep(.5)
    print('Browser did not open automatically. Open http://127.0.0.1:'+str(port)+' once the server is ready.',flush=True)


def start():
    check_python()
    if not PYTHON.is_file():raise RuntimeError('Run Setup.bat first.')
    env=tools_environment()
    if not valid_tools(env):raise RuntimeError('FFmpeg is missing. Run Setup.bat to install it.')
    try:port=int(os.environ.get('HYPIT_PORT','8081'))
    except ValueError as exc:raise RuntimeError('HYPIT_PORT must be a number, for example 8082.') from exc
    if not 1024<=port<=65535:raise RuntimeError('HYPIT_PORT must be between 1024 and 65535.')
    if studio_health(port):
        print('The studio is already running. Opening it in your browser.')
        webbrowser.open('http://127.0.0.1:'+str(port));return
    with socket.socket() as test:
        if test.connect_ex(('127.0.0.1',port))==0:
            raise RuntimeError('Port '+str(port)+' is used by another application. Close it or set HYPIT_PORT to another port.')
    print('Starting Hypit Studio. Keep this window open; Ctrl+C stops the server.\n',flush=True)
    process=subprocess.Popen([str(PYTHON),'-m','uvicorn','backend.server:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,env=env)
    threading.Thread(target=open_when_ready,args=(port,process),daemon=True).start()
    try:
        result=process.wait()
        if result:raise RuntimeError('The studio server stopped with exit code '+str(result)+'. See the console output above.')
    except KeyboardInterrupt:
        print('\nStopping the studio...')
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate();process.wait(timeout=5)


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(errors='replace')
    try:
        action=sys.argv[1] if len(sys.argv)>1 else 'start'
        if action=='setup':setup()
        elif action=='start':start()
        else:raise RuntimeError('Use windows.py setup or windows.py start.')
        return 0
    except KeyboardInterrupt:
        print('\nCancelled. Run Setup.bat or Start.bat again when ready.');return 1
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,zipfile.BadZipFile) as exc:
        print('\nERROR: '+str(exc));print('See START-HERE-WINDOWS.txt for help.');return 1

if __name__=='__main__':raise SystemExit(main())

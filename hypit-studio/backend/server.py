from __future__ import annotations
import hashlib
import json
import logging
import traceback
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

from . import media
from .providers import Providers, ProviderError, SubmissionUnknown

ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.getenv('HYPIT_DATA_DIR',str(ROOT/'.data'))).resolve()
DATA.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock()
POOL=ThreadPoolExecutor(max_workers=2)
PROVIDERS=Providers()
ACTIVE={'queued','working','uploading','submitted','polling','downloading'}

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Brief(Strict):
    product_name:str=Field(default='',max_length=120)
    product_facts:str=Field(default='',max_length=1200)
    character:str=Field(default='',max_length=500)
    environment:str=Field(default='',max_length=500)
    direction:str=Field(default='',max_length=500)
    cta:str=Field(default='',max_length=160)
    end_card:bool=True
    frame_provider:Literal['runware','wavespeed']='runware'
    video_provider:Literal['runware','wavespeed']='runware'
    lipsync_provider:Literal['runware','wavespeed']='runware'
    voice_provider:Literal['runware','wavespeed']='runware'
    runware_voice:str=Field(default='Sarah',max_length=60)
    wavespeed_voice:str=Field(default='English_CalmWoman',max_length=100)

class ShotEdit(Strict):
    description:str=Field(default='',max_length=1000)
    source_dialogue:str=Field(default='',max_length=1500)
    script:str=Field(default='',max_length=1000)
    changes:str=Field(default='',max_length=800)
    mode:Literal['performance','edit']='performance'
    audio_mode:Literal['original','revoice','rewrite','upload','mute']='rewrite'
    talking:bool=True

class ProjectEdit(Strict):
    name:str=Field(min_length=1,max_length=120)
    brief:Brief

class CreateProject(Strict):
    name:str=Field(min_length=1,max_length=120)

class Cuts(Strict):
    boundaries:list[float]=Field(min_length=2,max_length=121)

class Approval(Strict):
    approved:bool
    checks:dict[str,bool]=Field(default_factory=dict)

class Stage(Strict):
    stage:Literal['frame','video','voice','lipsync','analyze','transcribe','assemble']
    shot_id:str|None=None

class Resume(Strict):
    task_id:str|None=Field(default=None,max_length=200)

class Connection(Strict):
    runware:str|None=Field(default=None,max_length=1000)
    wavespeed:str|None=Field(default=None,max_length=1000)
    vision_model:str|None=Field(default=None,max_length=200)


def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return uuid.uuid4().hex

def directory(pid):
    if not re.fullmatch(r'[a-f0-9]{32}',pid): raise HTTPException(404,'Project not found.')
    return DATA/pid

def read(pid):
    path=directory(pid)/'project.json'
    if not path.exists(): raise HTTPException(404,'Project not found.')
    with LOCK:
        project=json.loads(path.read_text())
        if migrate_provider_settings(project):write(project)
        return project

def migrate_provider_settings(project):
    brief=project['brief'];changed=False
    for field in ('frame_provider','video_provider','voice_provider'):
        if brief.get(field)=='runway':brief[field]='runware';changed=True
    if 'runway_voice' in brief:
        brief.pop('runway_voice');brief['runware_voice']='Sarah';changed=True
    if 'runware_voice' not in brief:brief['runware_voice']='Sarah';changed=True
    if 'lipsync_provider' not in brief:
        # Existing projects used WaveSpeed lip sync; retain that explicit route.
        brief['lipsync_provider']='wavespeed';changed=True
    for shot in project['shots']:
        if shot.get('audio_mode')=='convert':shot['audio_mode']='revoice';changed=True
    if changed:
        project['revision']+=1
        project.setdefault('notices',[]).append('Provider settings migrated to Runware. Existing media and historical task IDs were preserved. Original-word revoicing now synthesizes the transcript; it does not convert source delivery.')
    return changed

def write(project):
    with LOCK:
        project['updated']=now()
        path=directory(project['id'])/'project.json'
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(project,indent=2))
        temp.replace(path)

def mutate(pid, fn):
    with LOCK:
        project=read(pid);fn(project);write(project)
        return project

def shot_by_id(project,sid):
    shot=next((s for s in project['shots'] if s['id']==sid),None)
    if not shot: raise HTTPException(404,'Shot not found.')
    return shot

def no_active(project):
    if any(j['status'] in ACTIVE for j in project['jobs']):
        raise HTTPException(409,'A job is running. Wait for it before changing this project.')

def file_path(pid,relative):
    path=(directory(pid)/relative).resolve()
    if not path.is_relative_to(directory(pid)):
        raise HTTPException(400,'Invalid asset path.')
    return path

def invalidate(shot,from_stage='frame'):
    if from_stage=='frame':
        shot['frame']=None;shot['frame_approved']=False
    if from_stage in {'frame','video'}:
        shot['video']=None
    if from_stage=='voice': shot['voice']=None
    shot['lipsync']=None;shot['approved']=False;shot['checks']={}

def invalidate_project(project):
    project['revision']+=1;project['export']=None
    for s in project['shots']:
        invalidate(s);s['voice']=None


def status():
    return {'runware':{'configured':PROVIDERS.configured('runware')},'wavespeed':{'configured':PROVIDERS.configured('wavespeed')},
        'vision_model':PROVIDERS.vision_model,'ffmpeg':bool(shutil.which('ffmpeg')),'ffprobe':bool(shutil.which('ffprobe')),
        'message':'API keys stay on this server. Project media is uploaded to a provider only when you request a generation or analysis.'}

@asynccontextmanager
async def lifespan(app):
    # A server restart must not resubmit an already-paid generation.
    for path in DATA.glob('*/project.json'):
        try:
            p=json.loads(path.read_text());changed=migrate_provider_settings(p)
            for job in p.get('jobs',[]):
                if job['status'] in ACTIVE:
                    job['status']='interrupted';job['error']='Server restarted. Resume an existing provider task; no generation has been resubmitted.';changed=True
            if changed: write(p)
        except (ValueError,OSError): continue
    yield

app=FastAPI(title='Hypit Studio Ad Remixer',lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])

@app.middleware('http')
async def local_only(request:Request, call_next):
    # This is a single-user local tool, not an unauthenticated public SaaS.
    if request.url.path.startswith('/api') and request.method not in {'GET','HEAD','OPTIONS'}:
        origin=request.headers.get('origin')
        if origin:
            parsed=urlparse(origin)
            if parsed.netloc!=request.headers.get('host') or parsed.scheme not in {'http','https'}:
                return JSONResponse({'detail':'Cross-origin requests are not allowed.'},status_code=403)
        if request.headers.get('x-hypit-client')!='studio':
            return JSONResponse({'detail':'Missing local client header.'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    if request.url.path.startswith('/api'):response.headers['Cache-Control']='no-store'
    return response

@app.exception_handler(media.MediaError)
@app.exception_handler(ProviderError)
async def known_error(request,exc):
    return JSONResponse({'detail':str(exc)},status_code=422)

@app.get('/api/health')
def health(): return {'ok':True,**status()}

@app.post('/api/connections')
def connections(body:Connection):
    for name in ('runware','wavespeed'):
        value=getattr(body,name)
        if value is not None and value.strip():
            if any(c.isspace() for c in value.strip()): raise HTTPException(422,'API keys must not contain whitespace.')
            PROVIDERS.keys[name]=value.strip()
            if name=='wavespeed':PROVIDERS.catalog_cache=None
    if body.vision_model is not None:PROVIDERS.vision_model=body.vision_model.strip()
    return status()

@app.post('/api/connections/{provider}/verify')
def verify(provider:str):
    if provider=='runware':
        data=PROVIDERS.runware_task({'taskType':'ping','taskUUID':str(uuid.uuid4())})
        if not data.get('pong'):raise ProviderError('Runware did not return the expected ping response.')
        return {'ok':True,'message':'Runware API authenticated and returned pong. Model schemas and eligibility are checked before generation.'}
    if provider=='wavespeed':
        entries=PROVIDERS.catalog(refresh=True)
        wanted=['google/nano-banana-2/edit','wavespeed-ai/wan-2.2/animate','bytedance/seedance-2.0/video-edit','minimax/speech-2.6-turbo']
        return {'ok':True,'message':f'WaveSpeed catalog loaded ({len(entries)} models).',
            'models':[{k:m.get(k) for k in ('model_id','name','base_price')} for m in entries if m.get('model_id') in wanted]}
    raise HTTPException(404,'Unknown provider.')

@app.get('/api/projects')
def projects():
    out=[]
    for path in DATA.glob('*/project.json'):
        try:
            p=json.loads(path.read_text());out.append({k:p[k] for k in ('id','name','updated','reference','export')})
        except (ValueError,OSError,KeyError): continue
    return sorted(out,key=lambda p:p['updated'],reverse=True)

@app.post('/api/projects',status_code=201)
def create(body:CreateProject):
    pid=uid();directory(pid).mkdir()
    p={'id':pid,'name':body.name,'brief':Brief().model_dump(),'assets':{},'reference':None,'shots':[], 'jobs':[], 'export':None,'revision':0,'updated':now()}
    write(p);return p

@app.get('/api/projects/{pid}')
def get_project(pid:str): return read(pid)

@app.put('/api/projects/{pid}')
def edit_project(pid:str,body:ProjectEdit):
    def update(p):
        no_active(p)
        changes={k for k,v in body.brief.model_dump().items() if p['brief'].get(k)!=v}
        if changes:
            p['revision']+=1;p['export']=None
            if changes & {'product_name','product_facts','character','environment','direction'}:
                for shot in p['shots']:invalidate(shot);shot['voice']=None
            elif changes & {'runware_voice','wavespeed_voice'}:
                for shot in p['shots']:invalidate(shot,'voice')
        p['name']=body.name;p['brief']=body.brief.model_dump()
    return mutate(pid,update)

@app.put('/api/projects/{pid}/shots/{sid}')
def edit_shot(pid:str,sid:str,body:ShotEdit):
    def update(p):
        no_active(p);s=shot_by_id(p,sid)
        changes={k for k,v in body.model_dump().items() if s.get(k)!=v}
        if changes:
            p['revision']+=1;p['export']=None
            if changes & {'description','changes'}:
                invalidate(s);s['voice']=None
            elif 'mode' in changes:invalidate(s,'video')
            elif changes & {'script','source_dialogue','audio_mode'}:invalidate(s,'voice')
            else:s['approved']=False;s['checks']={}
        s.update(body.model_dump())
    return mutate(pid,update)

async def save_upload(upload:UploadFile,destination:Path,limit=200*1024*1024):
    size=0
    try:
        with destination.open('wb') as stream:
            while chunk:=await upload.read(1024*1024):
                size+=len(chunk)
                if size>limit:raise HTTPException(413,f'File exceeds {limit//1024//1024}MB.')
                stream.write(chunk)
        if not size:raise HTTPException(422,'File is empty.')
    except Exception:
        destination.unlink(missing_ok=True);raise
    finally:await upload.close()

@app.post('/api/projects/{pid}/reference')
async def reference(pid:str,file:UploadFile=File(...)):
    with LOCK:
        p=read(pid);no_active(p)
        if p['reference']:raise HTTPException(409,'Create a new project for a different reference to preserve your existing work.')
        token=uid();path=directory(pid)/f'reference-{token}.mp4'
        # Reserve before writing to prevent overlapping uploads.
        p['jobs'].append({'id':token,'stage':'analyze-local','shot_id':None,'status':'working','created':now(),'revision':p['revision']});write(p)
    try:
        await save_upload(file,path)
        info=await __import__('asyncio').to_thread(media.probe,path)
        if info['has_video'] and not .25<=info['width']/info['height']<=4:
            raise HTTPException(422,'Use a reference aspect ratio between 1:4 and 4:1.')
        if not info['has_video'] or not .5<=info['duration']<=180:
            raise HTTPException(422,'Use a readable ad video between 0.5 and 180 seconds.')
        cuts=await __import__('asyncio').to_thread(media.detect_cuts,path,info['duration'])
        source={'file':path.name,**info}
        new_shots=await __import__('asyncio').to_thread(make_shots,pid,source,cuts)
        def done(p):
            p['reference']=source;p['shots']=new_shots;p['revision']+=1
            next(j for j in p['jobs'] if j['id']==token)['status']='succeeded'
        return mutate(pid,done)
    except Exception as exc:
        path.unlink(missing_ok=True)
        mutate(pid,lambda p:next(j for j in p['jobs'] if j['id']==token).update(status='failed',error=str(exc)))
        raise

def make_shots(pid,reference,cuts):
    result=[]
    for i,(start,end) in enumerate(zip(cuts,cuts[1:])):
        sid=uid();folder=directory(pid)/'shots'/sid
        media.extract_shot(file_path(pid,reference['file']),folder,start,end)
        s={'id':sid,'index':i+1,'start':start,'end':end,'duration':round(end-start,3),'folder':f'shots/{sid}',
            'frame':None,'frame_approved':False,'video':None,'voice':None,'lipsync':None,'approved':False,'checks':{},**ShotEdit().model_dump()}
        result.append(s)
    return result

@app.post('/api/projects/{pid}/cuts')
def cuts(pid:str,body:Cuts):
    with LOCK:
        p=read(pid);no_active(p)
        if not p['reference']:raise HTTPException(422,'Upload a reference first.')
        values=body.boundaries;duration=p['reference']['duration']
        if abs(values[0])>.001 or abs(values[-1]-duration)>.05 or any(not(.25<=b-a<=10.001) for a,b in zip(values,values[1:])):
            raise HTTPException(422,'Cuts must start at 0, end at the reference duration, and create shots between 0.25 and 10 seconds.')
        p['shots']=make_shots(pid,p['reference'],values);p['revision']+=1;p['export']=None;write(p)
        return p

@app.post('/api/projects/{pid}/assets/{role}')
async def asset(pid:str,role:str,file:UploadFile=File(...)):
    if role not in {'product','character','environment','music'}:raise HTTPException(422,'Unknown asset role.')
    # Local uploads are fast; a temporary job reservation keeps edits serialized.
    with LOCK:
        p=read(pid);no_active(p);token=uid()
        p['jobs'].append({'id':token,'stage':'upload','shot_id':None,'status':'working','created':now(),'revision':p['revision']});write(p)
    raw=directory(pid)/(token+'.upload');dest=directory(pid)/(token+('.wav' if role=='music' else '.jpg'))
    try:
        await save_upload(file,raw,30*1024*1024)
        if role=='music':
            info=media.probe(raw)
            if not info['has_audio']:raise HTTPException(422,'Upload a readable audio track.')
            media.ffmpeg(['-i',raw,'-vn','-t','180','-ac','2','-ar','48000',dest])
        else:media.clean_image(raw,dest)
        def done(p):
            if role!='music':invalidate_project(p)
            p['assets'][role]=dest.name;p['export']=None
            next(j for j in p['jobs'] if j['id']==token)['status']='succeeded'
        return mutate(pid,done)
    except Exception as exc:
        mutate(pid,lambda p:next(j for j in p['jobs'] if j['id']==token).update(status='failed',error=str(exc)));raise
    finally:raw.unlink(missing_ok=True)

@app.post('/api/projects/{pid}/shots/{sid}/upload/{kind}')
async def upload_take(pid:str,sid:str,kind:str,file:UploadFile=File(...)):
    if kind not in {'frame','video','voice','lipsync'}:raise HTTPException(422,'Unknown take type.')
    with LOCK:
        p=read(pid);no_active(p);shot_by_id(p,sid);token=uid()
        p['jobs'].append({'id':token,'stage':'upload','shot_id':sid,'status':'working','created':now(),'revision':p['revision']});write(p)
    raw=directory(pid)/(token+'.upload')
    try:
        await save_upload(file,raw)
        rel=f'shots/{sid}/{token}'+('.jpg' if kind=='frame' else '.wav' if kind=='voice' else '.mp4')
        dest=file_path(pid,rel);s=shot_by_id(read(pid),sid)
        if kind=='frame':media.clean_image(raw,dest)
        elif kind=='voice':media.fit_voice(raw,dest,s['duration'])
        else:
            info=media.probe(raw)
            if not info['has_video'] or info['duration']+.25<s['duration']:raise HTTPException(422,'The take must contain video and cover the full shot.')
            media.ffmpeg(['-i',raw,'-an','-c:v','libx264','-preset','fast','-movflags','+faststart',dest])
        def done(p):
            s=shot_by_id(p,sid)
            if kind!='lipsync':invalidate(s,kind)
            s[kind]=rel;s['approved']=False;p['export']=None
            if kind=='frame':s['frame_approved']=False
            next(j for j in p['jobs'] if j['id']==token)['status']='succeeded'
        return mutate(pid,done)
    except Exception as exc:
        mutate(pid,lambda p:next(j for j in p['jobs'] if j['id']==token).update(status='failed',error=str(exc)));raise
    finally:raw.unlink(missing_ok=True)

@app.post('/api/projects/{pid}/shots/{sid}/approve/{kind}')
def approve(pid:str,sid:str,kind:str,body:Approval):
    def update(p):
        no_active(p);s=shot_by_id(p,sid)
        if kind=='frame':
            if not s['frame']:raise HTTPException(422,'Generate or upload a frame first.')
            s['frame_approved']=body.approved
            if not body.approved:s['approved']=False
        elif kind=='shot':
            if body.approved:
                check_shot(s)
                required={'product','identity','setting','motion','audio','lipsync'}
                if not all(body.checks.get(k) for k in required):raise HTTPException(422,'Review every quality check before approving this shot.')
            s['approved']=body.approved;s['checks']=body.checks
        else:raise HTTPException(422,'Unknown approval type.')
        p['export']=None
    return mutate(pid,update)

def check_shot(s):
    if not s['video']:raise HTTPException(422,'Generate or upload the replacement video.')
    if s['audio_mode'] in {'rewrite','revoice','upload'}:
        if not s['voice']:raise HTTPException(422,'Generate or upload the replacement speech.')
        if s['talking'] and not s['lipsync']:raise HTTPException(422,'Lip-sync is required for a visible speaker with replacement speech. Run lip sync or upload a corrected take.')
    if s['audio_mode']=='original' and s['script'].strip() and s['script'].strip()!=s['source_dialogue'].strip():
        raise HTTPException(422,'The script changed, but original audio is selected. Choose rewritten speech or upload a new take.')


def stage_preflight(p,stage,s=None):
    if not p['reference']:raise HTTPException(422,'Upload a reference ad first.')
    b=p['brief']
    if stage in {'frame','video'}:
        if not b['product_name'] or not p['assets'].get('product'):raise HTTPException(422,'Add your product name and a clear product reference image.')
        if not s['description'].strip():raise HTTPException(422,'Describe the source shot, or run visual analysis, before generating.')
    if stage=='video' and not(s['frame'] and s['frame_approved']):raise HTTPException(422,'Approve the replacement frame before animating.')
    if stage=='voice':
        if s['audio_mode'] not in {'rewrite','revoice'}:raise HTTPException(422,'Select rewritten speech or original-word revoicing for this shot.')
        if s['audio_mode']=='rewrite' and not s['script'].strip():raise HTTPException(422,'Write the replacement dialogue first.')
        if s['audio_mode']=='revoice':
            if not s['source_dialogue'].strip():raise HTTPException(422,'Enter or transcribe the original words before revoicing.')
            if s['script'].strip() and s['script'].strip()!=s['source_dialogue'].strip():raise HTTPException(422,'Original-word revoicing keeps the transcript. Select rewritten speech to change the words.')
    if stage=='lipsync' and not (s['video'] and s['voice']):raise HTTPException(422,'Generate video and replacement speech before lip sync.')
    if stage=='assemble':
        if not p['shots'] or not all(s['approved'] for s in p['shots']):raise HTTPException(422,'Approve every replacement shot before assembly. Source clips will never silently fill missing shots.')
        for shot in p['shots']:check_shot(shot)
    if stage=='analyze' and not PROVIDERS.vision_model:raise HTTPException(422,'Set a vision-capable WaveSpeed LLM model in Connections, or describe shots manually.')
    provider=b['frame_provider'] if stage=='frame' else b['video_provider'] if stage=='video' else b['voice_provider'] if stage=='voice' else b['lipsync_provider'] if stage=='lipsync' else 'wavespeed'
    if stage!='assemble':PROVIDERS.key(provider)

@app.post('/api/projects/{pid}/jobs',status_code=202)
def new_job(pid:str,body:Stage):
    with LOCK:
        p=read(pid);no_active(p)
        if body.stage in {'frame','video','voice','lipsync'}:
            s=shot_by_id(p,body.shot_id)
        else:s=None
        # Ambiguous paid jobs must be reconciled or explicitly dismissed first.
        if any(j['status']=='unknown' and j['stage']==body.stage and j.get('shot_id')==body.shot_id for j in p['jobs']):
            raise HTTPException(409,'An earlier submission has an unknown outcome. Recover its task ID or dismiss it after checking your provider dashboard.')
        stage_preflight(p,body.stage,s)
        job={'id':uid(),'stage':body.stage,'shot_id':body.shot_id,'status':'queued','created':now(),'revision':p['revision'],'task_id':None,'provider':None,'error':None}
        p['jobs'].append(job);write(p)
        POOL.submit(execute,pid,job['id'],False)
        return job

@app.post('/api/projects/{pid}/jobs/{jid}/resume',status_code=202)
def resume(pid:str,jid:str,body:Resume):
    with LOCK:
        p=read(pid);no_active(p);job=next((j for j in p['jobs'] if j['id']==jid),None)
        if not job:raise HTTPException(404,'Job not found.')
        if job['revision']!=p['revision']:raise HTTPException(409,'Project inputs changed. This result belongs to an older brief and cannot be applied.')
        if job['status'] not in {'interrupted','failed','unknown'}:raise HTTPException(409,'This job cannot be resumed.')
        if job.get('provider')=='runway':raise HTTPException(409,'This is a legacy-provider task. It cannot be resumed through Runware. Existing output files remain available.')
        if body.task_id:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',body.task_id):raise HTTPException(422,'Invalid provider task ID.')
            job['task_id']=body.task_id
        if not job.get('task_id') or not job.get('provider'):raise HTTPException(422,'No remote task to resume. Obtain its task ID from the provider dashboard. Local processing can be run again.')
        job['status']='queued';job['error']=None;write(p)
        POOL.submit(execute,pid,jid,True);return job

@app.post('/api/projects/{pid}/jobs/{jid}/dismiss')
def dismiss(pid:str,jid:str):
    def update(p):
        no_active(p);job=next((j for j in p['jobs'] if j['id']==jid),None)
        if not job or job['status'] not in {'unknown','failed','interrupted'}:raise HTTPException(422,'Only a stopped job can be dismissed.')
        job['status']='dismissed'
    return mutate(pid,update)


def job_update(pid,jid,**values):
    def update(p):next(j for j in p['jobs'] if j['id']==jid).update(values)
    mutate(pid,update)

def frame_prompt(p,s):
    b=p['brief']
    return (f'Remake @composition with the same framing, pose, hand placement, shot scale, and perspective. '
        f'Source: {s["description"]}. Replace the showcased item with {b["product_name"]} from @product. '
        f'Match product silhouette, materials and packaging; remove the old product and old branding. '
        f'Cast: {b["character"] or "keep the original cast"}. Setting: {b["environment"] or "keep the original setting"}. '
        f'@identity supplies the replacement cast and/or room references when present. {s["changes"]}. {b["direction"]}. '
        'Photorealistic UGC still. No new captions, lettering, or extra products.')

def video_prompt(p,s):
    b=p['brief']
    return (f'Preserve the input video camera path, action beats, timing and hand-object interactions. '
        f'{s["description"]}. Use the approved replacement frame for the cast, room, lighting and {b["product_name"]}. '
        f'{s["changes"]} {b["direction"]} No new text, logos, objects or cuts. Do not revert to the original product or actor.')


def prepare_request(p,s,stage,job):
    pid=p['id'];b=p['brief'];folder=file_path(pid,s['folder']) if s else None
    if stage=='frame':
        provider=b['frame_provider'];prompt=frame_prompt(p,s)
        if provider=='runware':PROVIDERS.runware_schema('google:4@3')
        composition=PROVIDERS.upload(provider,folder/'first.jpg')
        product=PROVIDERS.upload(provider,file_path(pid,p['assets']['product']))
        identity_paths=[file_path(pid,p['assets'][r]) for r in ('character','environment') if p['assets'].get(r)]
        refs=[composition,product]+[PROVIDERS.upload(provider,path) for path in identity_paths]
        prompt=prompt.replace('@composition','image 1 (composition)').replace('@product','image 2 (product)').replace('@identity','subsequent images (cast / room)')
        prompt+=' Preserve the full source framing and aspect ratio of image 1.'
        if provider=='runware':
            return provider,'google:4@3','imageInference',{'positivePrompt':prompt,'resolution':'1K','inputs':{'referenceImages':refs},'outputType':'URL'}
        source_ratio=p['reference']['width']/p['reference']['height']
        aspect=min(['9:16','16:9','1:1','4:5','5:4','3:4','4:3','2:3','3:2','21:9','1:4','4:1'],key=lambda r:abs(int(r.split(':')[0])/int(r.split(':')[1])-source_ratio))
        return provider,'google/nano-banana-2/edit',None,{'prompt':prompt,'images':refs,'aspect_ratio':aspect,'resolution':'1k'}
    if stage=='video':
        provider=b['video_provider']
        model='prunaai:p-video@animate' if s['mode']=='performance' else 'luma:ray@3.2'
        if provider=='runware':PROVIDERS.runware_schema(model)
        frame=PROVIDERS.upload(provider,file_path(pid,s['frame']))
        video=PROVIDERS.upload(provider,folder/'driving.mp4')
        if provider=='runware':
            if s['mode']=='performance':
                return provider,model,'videoInference',{'inputs':{'referenceImages':[frame],'referenceVideos':[video]},'resolution':'720p','settings':{'preserveAudio':False}}
            return provider,model,'videoInference',{'inputs':{'video':video,'frameImages':[{'image':frame,'frame':'first'}]},'positivePrompt':video_prompt(p,s),'settings':{'edit':{'autoControls':True}}}
        if s['mode']=='performance':
            return provider,'wavespeed-ai/wan-2.2/animate',None,{'image':frame,'video':video,'mode':'animate','resolution':'720p'}
        return provider,'bytedance/seedance-2.0/video-edit',None,{'video':video,'prompt':video_prompt(p,s),'reference_images':[frame],'resolution':'720p'}
    if stage=='voice':
        provider=b['voice_provider'];text=s['source_dialogue'] if s['audio_mode']=='revoice' else s['script']
        if provider=='runware':
            PROVIDERS.runware_schema('inworld:tts@2')
            return provider,'inworld:tts@2','audioInference',{'speech':{'text':text,'voice':b['runware_voice']},'settings':{'textNormalization':True},'outputType':'URL'}
        return provider,'minimax/speech-2.6-turbo',None,{'text':text,'voice_id':b['wavespeed_voice'],'emotion':'neutral','format':'wav'}
    if stage=='lipsync':
        provider=b['lipsync_provider']
        if provider=='runware':
            PROVIDERS.runware_schema('sync:3@0')
            return provider,'sync:3@0','videoInference',{'inputs':{'video':PROVIDERS.upload(provider,file_path(pid,s['video'])),'audio':PROVIDERS.upload(provider,file_path(pid,s['voice']))}}
        model=PROVIDERS.find_model('lipsync')
        return provider,model,None,{'video':PROVIDERS.upload(provider,file_path(pid,s['video'])),'audio':PROVIDERS.upload(provider,file_path(pid,s['voice']))}
    if stage=='transcribe':
        if not p['reference']['has_audio']:raise ProviderError('Reference has no audio track.')
        audio=directory(pid)/(job['id']+'-source.wav')
        media.ffmpeg(['-i',file_path(pid,p['reference']['file']),'-vn','-ar','16000','-ac','1',audio])
        return 'wavespeed',PROVIDERS.find_model('transcribe'),None,{'audio':PROVIDERS.upload('wavespeed',audio)}
    raise ProviderError('Unsupported generation stage.')


def complete_output(p,job,path):
    pid=p['id'];stage=job['stage'];sid=job.get('shot_id');s=shot_by_id(p,sid) if sid else None
    if stage=='transcribe':
        try:
            transcript=json.loads(path.read_text())
            segments=transcript.get('segments')
            if not isinstance(segments,list) or not all(isinstance(x,dict) and 'start' in x and 'end' in x and 'text' in x for x in segments):raise ValueError()
        except (ValueError,UnicodeError,TypeError):raise ProviderError('Transcription output has no timestamped segments. Enter source dialogue per shot; no transcript was guessed.')
        def save_transcript(p):
            for s in p['shots']:
                s['source_dialogue']=' '.join(seg['text'].strip() for seg in segments if s['start']<= (float(seg['start'])+float(seg['end']))/2 < s['end'])
        mutate(pid,save_transcript);return
    ext='.jpg' if stage=='frame' else '.wav' if stage=='voice' else '.mp4'
    rel=s['folder']+'/'+job['id']+ext;dest=file_path(pid,rel)
    meta=None
    if stage=='frame':media.clean_image(path,dest)
    elif stage=='voice':meta=media.fit_voice(path,dest,s['duration'])
    else:
        info=media.probe(path)
        if not info['has_video']:raise ProviderError('Provider output is not a valid video.')
        if info['duration']+.25<s['duration']:raise ProviderError('Generated video does not cover this shot. Retry the shot with a longer input.')
        # Source audio is selected separately; never inherit random generated speech.
        media.ffmpeg(['-i',path,'-an','-c:v','libx264','-preset','fast','-crf','18','-movflags','+faststart',dest])
    def save_take(p):
        s=shot_by_id(p,sid)
        if stage!='lipsync':invalidate(s,stage)
        s[stage]=rel;s['approved']=False;p['export']=None
        if meta:s['voice_fit']=meta
    mutate(pid,save_take)
    job_update(pid,job['id'],artifact=rel)


def assembly(p,job):
    pid=p['id'];out=directory(pid)/'exports'/job['id'];out.mkdir(parents=True)
    reference=p['reference'];ratio=reference['width']/reference['height']
    size=(720,max(2,round(720/ratio/2)*2)) if ratio<1 else (max(2,round(720*ratio/2)*2),720)
    parts=[];durations=[]
    for s in p['shots']:
        check_shot(s)
        src=file_path(pid,s['lipsync'] or s['video'])
        audio=file_path(pid,s['voice']) if s['audio_mode'] in {'rewrite','revoice','upload'} else file_path(pid,s['folder']+'/source.wav') if s['audio_mode']=='original' and reference['has_audio'] else None
        dest=out/(s['id']+'.mp4')
        duration=(round(s['end']*30)-round(s['start']*30))/30
        media.normalize_clip(src,dest,duration,size,audio)
        parts.append(dest);durations.append(duration)
    assembled=out/'clean.mp4'
    media.assemble(parts,assembled,file_path(pid,p['assets']['music']) if p['assets'].get('music') else None,durations)
    final=out/'ad-remake.mp4'
    if p['brief']['end_card'] and p['brief']['cta'].strip():
        product=file_path(pid,p['assets']['product']) if p['assets'].get('product') else None
        media.add_brand(assembled,final,product,p['brief']['cta'],min(2,reference['duration']))
    else:shutil.copyfile(assembled,final)
    info=media.probe(final)
    if abs(info['duration']-reference['duration'])>.15:
        raise media.MediaError('Final duration differs from the reference. Adjust cut boundaries to 30fps frame times and assemble again.')
    report={'reference_duration':reference['duration'],'output_duration':info['duration'],'shot_count':len(p['shots']),
        'timing_tolerance_seconds':.15,'quality_review':'Manual approval recorded for every shot. Not an automated fidelity guarantee.',
        'shots':[{'id':s['id'],'start':s['start'],'end':s['end'],'checks':s['checks'],'audio_mode':s['audio_mode']} for s in p['shots']]}
    (out/'review.json').write_text(json.dumps(report,indent=2))
    mutate(pid,lambda p:p.update(export={'file':str(final.relative_to(directory(pid))),'report':str((out/'review.json').relative_to(directory(pid))),**info}))


def execute(pid,jid,resuming=False):
    try:
        p=read(pid);job=next(j for j in p['jobs'] if j['id']==jid);stage=job['stage']
        if job['revision']!=p['revision']:raise ProviderError('Inputs changed; stopped to avoid applying a stale result.')
        job_update(pid,jid,status='working')
        if stage=='assemble':assembly(p,job);job_update(pid,jid,status='succeeded');return
        if stage=='analyze':
            shots=[{'id':s['id'],'duration':s['duration'],'source_dialogue':s['source_dialogue'],
                'frames':[file_path(pid,s['folder']+'/'+n+'.jpg') for n in ('first','middle','last')]} for s in p['shots']]
            result=PROVIDERS.vision(shots,p['brief'])
            edits={s['id']:s for s in result['shots'] if isinstance(s,dict) and isinstance(s.get('id'),str)}
            if any(shot['id'] not in edits or not isinstance(edits[shot['id']].get('description'),str) or not edits[shot['id']]['description'].strip() for shot in p['shots']):
                raise ProviderError('Visual analysis did not cover every shot. No partial analysis was applied. Retry with fewer shots or describe them manually.')
            def save_analysis(p):
                for s in p['shots']:
                    if s['id'] in edits:
                        entry=edits[s['id']]
                        description=entry.get('description','');script=entry.get('replacement_script','')
                        if not isinstance(description,str) or not isinstance(script,str):raise ProviderError('Invalid shot analysis format.')
                        invalidate(s);s['voice']=None
                        s['description']=description[:1000]
                        # A vision-only call cannot hear the ad. Never invent missing dialogue.
                        if s['source_dialogue'].strip():s['script']=script[:1000]
                p['revision']+=1;p['export']=None
            mutate(pid,save_analysis);job_update(pid,jid,status='succeeded');return
        s=shot_by_id(p,job['shot_id']) if job.get('shot_id') else None
        if not resuming:
            job_update(pid,jid,status='uploading')
            provider,model,route,payload=prepare_request(p,s,stage,job)
            values={'provider':provider,'model':model,'status':'submitted'}
            if provider=='runware':
                payload['taskUUID']=str(uuid.UUID(jid));values['task_id']=payload['taskUUID']
            job_update(pid,jid,**values)
            result=PROVIDERS.submit(provider,model,payload,route)
            job['provider']=provider;job['task_id']=result['id']
            job_update(pid,jid,task_id=result['id'],estimated_cost=result.get('estimatedCost'),status='polling')
        provider=job['provider'];task_id=job['task_id']
        deadline=time.monotonic()+1800;failure_count=0
        while time.monotonic()<deadline:
            try:result=PROVIDERS.poll(provider,task_id);failure_count=0
            except ProviderError:
                failure_count+=1
                if failure_count>=3:raise
                time.sleep(4*failure_count);continue
            if result['done']:
                job_update(pid,jid,status='downloading',cost=result.get('cost'))
                path=directory(pid)/(jid+'.result')
                PROVIDERS.download(result['outputs'][0],path)
                complete_output(p,job,path);path.unlink(missing_ok=True)
                job_update(pid,jid,status='succeeded');return
            job_update(pid,jid,status='polling',remote_status=result['status'])
            time.sleep(5)
        job_update(pid,jid,status='interrupted',error='Polling stopped after 30 minutes. Resume this task to continue polling without creating another paid job.')
    except SubmissionUnknown as exc:job_update(pid,jid,status='unknown',error=str(exc))
    except Exception as exc:
        if not isinstance(exc,(ProviderError,media.MediaError,HTTPException,ValueError)):
            logging.getLogger('hypit').error('Job %s failed: %s: %s\n%s', jid, type(exc).__name__, PROVIDERS.safe_error(str(exc)), ''.join(traceback.format_tb(exc.__traceback__)))
        message=PROVIDERS.safe_error(str(exc)) if isinstance(exc,(ProviderError,media.MediaError,HTTPException,ValueError)) else 'Processing failed. Check local server diagnostics and retry the affected step.'
        job_update(pid,jid,status='failed',error=message)

@app.get('/api/projects/{pid}/shots/{sid}/preview')
def shot_preview(pid:str,sid:str):
    p=read(pid);s=shot_by_id(p,sid)
    source=s['lipsync'] or s['video']
    if not source:raise HTTPException(422,'No replacement video to preview.')
    audio=s['voice'] if s['audio_mode'] in {'rewrite','revoice','upload'} else s['folder']+'/source.wav' if s['audio_mode']=='original' and p['reference']['has_audio'] else None
    signature=hashlib.sha256(json.dumps([source,audio,s['duration']]).encode()).hexdigest()[:24]
    preview_dir=directory(pid)/'previews';preview_dir.mkdir(exist_ok=True)
    destination=preview_dir/(signature+'.mp4')
    if not destination.exists():
        info=media.probe(file_path(pid,source))
        ratio=info['width']/info['height']
        size=(360,max(2,round(360/ratio/2)*2)) if ratio<1 else (max(2,round(360*ratio/2)*2),360)
        temp=preview_dir/(uid()+'.mp4')
        try:
            media.normalize_clip(file_path(pid,source),temp,s['duration'],size,file_path(pid,audio) if audio else None)
            temp.replace(destination)
        finally:temp.unlink(missing_ok=True)
    return FileResponse(destination,media_type='video/mp4')

@app.get('/api/projects/{pid}/media/{path:path}')
def get_media(pid:str,path:str):
    p=read(pid);target=file_path(pid,path)
    if not target.is_file() or target.suffix.lower() not in {'.mp4','.wav','.jpg','.png','.json'} or target.name=='project.json':
        raise HTTPException(404,'Media not found.')
    return FileResponse(target,filename=target.name if target.name in {'ad-remake.mp4','review.json'} else None)

# Expose only frontend files, never .data, keys, backend source or tests.
@app.get('/')
def index():return FileResponse(ROOT/'index.html')

@app.get('/{filename}')
def static_file(filename:str):
    if filename not in {'index.html','app.js','style.css','remix.js','remix.css'}:raise HTTPException(404,'Not found.')
    return FileResponse(ROOT/filename)

"""Offline contract + real-media integration tests. No paid API calls."""
from __future__ import annotations
import io
import json
import os
import tempfile
import time
import unittest
import uuid
import base64
import wave
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from PIL import Image

TEMP=tempfile.TemporaryDirectory(prefix='hypit-tests-')
os.environ['HYPIT_DATA_DIR']=TEMP.name
from backend import media,server
from backend.providers import Providers,ProviderError,SubmissionUnknown

HEADERS={'X-Hypit-Client':'studio'}
WAVE_SCHEMAS={
 'google/nano-banana-2/edit':{'prompt':{'type':'string'},'images':{'type':'array'},'aspect_ratio':{'type':'string'},'resolution':{'type':'string'}},
 'wavespeed-ai/wan-2.2/animate':{'image':{'type':'string'},'video':{'type':'string'},'mode':{'enum':['animate','replace']},'resolution':{'type':'string'}},
 'bytedance/seedance-2.0/video-edit':{'video':{'type':'string'},'prompt':{'type':'string'},'reference_images':{'type':'array'},'resolution':{'type':'string'}},
 'minimax/speech-2.6-turbo':{'text':{'type':'string'},'voice_id':{'type':'string'},'emotion':{'type':'string'},'format':{'type':'string'}},
 'sync/lipsync-2-pro':{'video':{'type':'string'},'audio':{'type':'string'}},
}
CATALOG=[{'model_id':model,'api_schema':{'api_schemas':[{'request_schema':{'type':'object','properties':props,'required':list(props)}}]}} for model,props in WAVE_SCHEMAS.items()]

def runware_schema(model):
 common={'taskType':{'enum':['imageInference','videoInference','audioInference']},'taskUUID':{'type':'string'},'model':{'const':model},
  'deliveryMethod':{'enum':['async']},'includeCost':{'type':'boolean'},'numberResults':{'const':1}}
 strings={'type':'array','items':{'type':'string'},'minItems':1}
 def obj(properties,required=None):return {'type':'object','properties':properties,'required':required or list(properties),'additionalProperties':False}
 specific={
  'google:4@3':{'positivePrompt':{'type':'string'},'resolution':{'enum':['1K']},'inputs':obj({'referenceImages':strings}),'outputType':{'enum':['URL']}},
  'prunaai:p-video@animate':{'inputs':obj({'referenceImages':{**strings,'maxItems':1},'referenceVideos':{**strings,'maxItems':1}}),'resolution':{'enum':['720p']},'settings':obj({'preserveAudio':{'type':'boolean'}})},
  'luma:ray@3.2':{'positivePrompt':{'type':'string'},'inputs':obj({'video':{'type':'string'},'frameImages':{'type':'array','minItems':1}}),'settings':obj({'edit':obj({'autoControls':{'const':True}})})},
  'inworld:tts@2':{'speech':obj({'text':{'type':'string'},'voice':{'enum':['Sarah','Ashley','Mark']}}),'settings':obj({'textNormalization':{'type':'boolean'}}),'outputType':{'enum':['URL']}},
  'sync:3@0':{'inputs':obj({'video':{'type':'string'},'audio':{'type':'string'}})}
 }
 props={**common,**specific[model]}
 return {'$schema':'https://json-schema.org/draft/2020-12/schema',**obj(props,[k for k in props if k!='numberResults'])}

class ProviderContracts(unittest.TestCase):
 def test_runware_batch_upload_schema_and_task_poll(self):
  calls=[];task_id=str(uuid.uuid4());asset_id=str(uuid.uuid4())
  def mock(req):
   calls.append(req)
   if req.url.host=='schemas.runware.ai':
    self.assertNotIn('authorization',req.headers)
    return httpx.Response(200,json={'requestSchema':runware_schema('prunaai:p-video@animate')})
   task=json.loads(req.content)[0]
   if task['taskType']=='mediaStorage':return httpx.Response(200,json={'data':[{'taskUUID':task['taskUUID'],'mediaUUID':asset_id}]})
   if task['taskType']=='getResponse':return httpx.Response(200,json={'data':[{'taskUUID':task_id,'status':'success','videoURL':'https://assets.example.test/video.mp4','cost':.24}]})
   return httpx.Response(200,json={'data':[{'taskUUID':task_id,'status':'processing'}]})
  p=Providers(httpx.Client(transport=httpx.MockTransport(mock)));p.keys['runware']='test-not-a-real-key'
  source=Path(TEMP.name)/'source.jpg';source.write_bytes(b'image-bytes')
  self.assertEqual(p.upload('runware',source),asset_id)
  upload=json.loads(calls[0].content)[0]
  self.assertEqual(upload['operation'],'upload');self.assertEqual(base64.b64decode(upload['media']),b'image-bytes')
  self.assertEqual(calls[0].url.host,'api.runware.ai');self.assertEqual(calls[0].url.path,'/v1')
  self.assertNotIn('X-Runway-Version',calls[0].headers)
  p.submit('runware','prunaai:p-video@animate',{'taskUUID':task_id,'inputs':{'referenceImages':[asset_id],'referenceVideos':[asset_id]},'resolution':'720p','settings':{'preserveAudio':False}},'videoInference')
  body=json.loads(calls[2].content)
  self.assertIsInstance(body,list);self.assertEqual(body[0]['deliveryMethod'],'async');self.assertEqual(body[0]['taskUUID'],task_id)
  result=p.poll('runware',task_id)
  self.assertTrue(result['done']);self.assertEqual(result['cost'],.24)
  poll=json.loads(calls[-1].content)[0];self.assertEqual(poll,{'taskType':'getResponse','taskUUID':task_id})

 def test_runware_http_200_errors_and_schema_rejections(self):
  calls=[]
  def mock(req):calls.append(req);return httpx.Response(200,json={'errors':[{'code':'invalidApiKey','message':'Invalid API key'}]})
  p=Providers(httpx.Client(transport=httpx.MockTransport(mock)));p.keys['runware']='fake'
  with self.assertRaisesRegex(ProviderError,'Invalid API key'):p.runware_task({'taskType':'ping','taskUUID':str(uuid.uuid4())})
  p.runware_schema_cache['inworld:tts@2']=(time.time(),runware_schema('inworld:tts@2'))
  with self.assertRaisesRegex(ProviderError,'violates the current Runware schema'):
   p.submit('runware','inworld:tts@2',{'taskUUID':str(uuid.uuid4()),'speech':{'text':'Hello','voice':'Maya'},'settings':{'textNormalization':True},'outputType':'URL'},'audioInference')
  self.assertEqual(len(calls),1)

 def test_wave_live_schema_envelope_upload_and_poll(self):
  calls=[]
  def mock(req):
   calls.append(req)
   if req.url.path=='/api/v3/models':return httpx.Response(200,json={'code':200,'data':CATALOG})
   if req.url.path=='/api/v3/media/uploads':return httpx.Response(200,json={'code':200,'data':{'upload':{'method':'PUT','url':'https://assets.example.test/put','headers':{'x-signed':'yes'}},'download_url':'https://assets.example.test/upload.jpg'}})
   if req.url.path=='/put':return httpx.Response(200)
   if req.method=='POST':return httpx.Response(200,json={'code':200,'data':{'id':'wave123'}})
   return httpx.Response(200,json={'code':200,'data':{'status':'completed','outputs':['https://assets.example.test/out.mp4']}})
  p=Providers(httpx.Client(transport=httpx.MockTransport(mock)));p.keys['wavespeed']='test-not-a-real-key'
  source=Path(TEMP.name)/'source.jpg';source.write_bytes(b'image')
  with patch.object(p,'public_url',side_effect=lambda url:url):self.assertEqual(p.upload('wavespeed',source),'https://assets.example.test/upload.jpg')
  self.assertNotIn('authorization',calls[1].headers)
  self.assertEqual(p.find_model('lipsync'),'sync/lipsync-2-pro')
  result=p.submit('wavespeed','sync/lipsync-2-pro',{'video':'https://assets.example.test/video','audio':'https://assets.example.test/audio'})
  self.assertEqual(result['id'],'wave123');self.assertTrue(p.poll('wavespeed','wave123')['done'])
  before=len(calls)
  with self.assertRaises(ProviderError):p.submit('wavespeed','sync/lipsync-2-pro',{'video':'url','audio':'url','fake_reference':'url'})
  self.assertEqual(len(calls),before)

 def test_paid_timeout_is_never_retried(self):
  calls=[]
  def mock(req):calls.append(req);raise httpx.ReadTimeout('simulated',request=req)
  p=Providers(httpx.Client(transport=httpx.MockTransport(mock)));p.keys['runware']='fake'
  p.runware_schema_cache['google:4@3']=(time.time(),runware_schema('google:4@3'))
  with self.assertRaises(SubmissionUnknown):p.submit('runware','google:4@3',{'taskUUID':str(uuid.uuid4()),'positivePrompt':'x','resolution':'1K','inputs':{'referenceImages':['asset']},'outputType':'URL'},'imageInference')
  self.assertEqual(len(calls),1)

 def test_private_asset_urls_rejected(self):
  for url in ['http://example.com/a','https://127.0.0.1/a','https://169.254.169.254/a','https://localhost/a','https://user:secret@example.com/a','https://example.com:8080/a']:
   with self.subTest(url=url),self.assertRaises(ProviderError):Providers.public_url(url)

class Workflow(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.fixtures=Path(TEMP.name)/'fixtures';cls.fixtures.mkdir()
  f=cls.fixtures
  media.ffmpeg(['-f','lavfi','-i','color=c=red:s=180x320:r=30:d=4','-f','lavfi','-i','color=c=blue:s=180x320:r=30:d=4',
   '-f','lavfi','-i','sine=frequency=440:duration=8','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]',
   '-map','[v]','-map','2:a','-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',f/'reference.mp4'])
  media.ffmpeg(['-f','lavfi','-i','color=c=green:s=180x320:r=30:d=4','-f','lavfi','-i','sine=frequency=880:duration=4',
   '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',f/'replacement.mp4'])
  media.ffmpeg(['-f','lavfi','-i','sine=frequency=660:duration=4','-ar','48000',f/'voice.wav'])
  media.ffmpeg(['-f','lavfi','-i','sine=frequency=660:duration=9','-ar','48000',f/'too-long.wav'])
  image=Image.new('RGB',(180,320),'green');image.save(f/'frame.jpg')
  cls.calls=[];cls.remote={};cls.submit_count=0
  def mock(req):
   cls.calls.append(req)
   if req.url.host=='schemas.runware.ai':
    from urllib.parse import unquote
    model=unquote(req.url.path.removeprefix('/resolve/'))
    return httpx.Response(200,json={'requestSchema':runware_schema(model)})
   if req.url.host=='assets.example.test':
    if req.method in {'PUT','POST'}:return httpx.Response(200)
    name=req.url.path.split('/')[-1]
    mapping={'frame.jpg':f/'frame.jpg','voice.wav':f/'voice.wav','video.mp4':f/'replacement.mp4'}
    return httpx.Response(200,content=mapping[name].read_bytes())
   path=req.url.path
   if req.url.host=='api.runware.ai':
    task=json.loads(req.content)[0];tid=task['taskUUID'];kind=task['taskType']
    if kind=='mediaStorage':data={'taskUUID':tid,'mediaUUID':str(uuid.uuid4())}
    elif kind=='ping':data={'taskType':'ping','pong':True}
    elif kind=='getResponse':
     asset=cls.remote[tid];field='imageURL' if asset=='frame.jpg' else 'audioURL' if asset=='voice.wav' else 'videoURL'
     data={'taskUUID':tid,'status':'success',field:f'https://assets.example.test/{asset}','cost':.05}
    else:
     cls.submit_count+=1
     cls.remote[tid]='frame.jpg' if kind=='imageInference' else 'voice.wav' if kind=='audioInference' else 'video.mp4'
     data={'taskUUID':tid,'status':'processing'}
    return httpx.Response(200,json={'data':[data]})
   if path=='/api/v3/models':return httpx.Response(200,json={'code':200,'data':CATALOG})
   if path=='/api/v3/media/uploads':return httpx.Response(200,json={'code':200,'data':{'upload':{'method':'PUT','url':'https://assets.example.test/upload','headers':{}},'download_url':'https://assets.example.test/uploaded'}})
   if req.method=='POST':
    model=path.removeprefix('/api/v3/');cls.submit_count+=1;task='task'+str(cls.submit_count)
    asset='frame.jpg' if model=='google/nano-banana-2/edit' else 'voice.wav' if model=='minimax/speech-2.6-turbo' else 'video.mp4'
    cls.remote[task]=asset;data={'id':task}
   else:
    task=path.split('/')[4];asset=cls.remote[task]
    data={'status':'completed','outputs':[f'https://assets.example.test/{asset}']}
   return httpx.Response(200,json={'code':200,'data':data})
  cls.providers=Providers(httpx.Client(transport=httpx.MockTransport(mock)))
  cls.providers.keys={'runware':'test-not-a-real-key','wavespeed':'test-not-a-real-key'}
  cls.providers.public_url=lambda url:url
  cls.old=server.PROVIDERS;server.PROVIDERS=cls.providers
  cls.client_ctx=TestClient(server.app);cls.client=cls.client_ctx.__enter__()

 @classmethod
 def tearDownClass(cls):
  cls.client_ctx.__exit__(None,None,None);server.PROVIDERS=cls.old

 def request(self,method,path,**kwargs):
  response=self.client.request(method,'/api'+path,headers=HEADERS,**kwargs)
  self.assertLess(response.status_code,300,response.text)
  return response.json()
 def get(self,pid):return self.request('GET','/projects/'+pid)
 def job(self,pid,stage,sid=None):
  result=self.request('POST',f'/projects/{pid}/jobs',json={'stage':stage,'shot_id':sid})
  deadline=time.time()+60
  while time.time()<deadline:
   p=self.get(pid);job=next(j for j in p['jobs'] if j['id']==result['id'])
   if job['status'] not in server.ACTIVE:
    self.assertEqual(job['status'],'succeeded',job)
    return p
   time.sleep(.05)
  self.fail('Job did not finish: '+stage)

 def create_reference(self):
  p=self.request('POST','/projects',json={'name':'UGC integration test'})
  with (self.fixtures/'reference.mp4').open('rb') as f:p=self.request('POST',f'/projects/{p["id"]}/reference',files={'file':('reference.mp4',f,'video/mp4')})
  return p

 def test_end_to_end_remake_both_providers(self):
  p=self.create_reference();pid=p['id']
  self.assertEqual(len(p['shots']),2)
  self.assertAlmostEqual(p['shots'][0]['end'],4,delta=.06)
  brief={**p['brief'],'product_name':'New product','product_facts':'A green bottle. No health claims.','character':'New presenter','environment':'A bright room','cta':'New product · Shop now'}
  p=self.request('PUT',f'/projects/{pid}',json={'name':p['name'],'brief':brief})
  with (self.fixtures/'frame.jpg').open('rb') as f:p=self.request('POST',f'/projects/{pid}/assets/product',files={'file':('product.jpg',f,'image/jpeg')})
  # Change the default provider by shot without changing the brief during production.
  # Both contracts are exercised: Runware frames/motion/voice/lip sync, then WaveSpeed adapters.
  for s in p['shots']:
   sid=s['id'];values=server.ShotEdit(description='Presenter holds the product up to camera.',source_dialogue='Old product.',script='Our new product.',audio_mode='rewrite').model_dump()
   self.request('PUT',f'/projects/{pid}/shots/{sid}',json=values)
   p=self.job(pid,'frame',sid)
   # Reject animation before frame approval.
   bad=self.client.post(f'/api/projects/{pid}/jobs',headers=HEADERS,json={'stage':'video','shot_id':sid})
   self.assertEqual(bad.status_code,422)
   self.request('POST',f'/projects/{pid}/shots/{sid}/approve/frame',json={'approved':True})
   p=self.job(pid,'video',sid);p=self.job(pid,'voice',sid)
   # On-camera rewritten speech cannot be approved before lip sync.
   checks={k:True for k in ['product','identity','setting','motion','audio','lipsync']}
   bad=self.client.post(f'/api/projects/{pid}/shots/{sid}/approve/shot',headers=HEADERS,json={'approved':True,'checks':checks})
   self.assertEqual(bad.status_code,422)
   p=self.job(pid,'lipsync',sid)
   preview=self.client.get(f'/api/projects/{pid}/shots/{sid}/preview')
   self.assertEqual(preview.status_code,200)
   self.assertTrue(preview.headers['content-type'].startswith('video/mp4'))
   p=self.request('POST',f'/projects/{pid}/shots/{sid}/approve/shot',json={'approved':True,'checks':checks})
  p=self.job(pid,'assemble');self.assertIsNotNone(p['export'])
  info=media.probe(server.file_path(pid,p['export']['file']))
  self.assertAlmostEqual(info['duration'],8,delta=.15);self.assertTrue(info['has_audio'])
  report=json.loads(server.file_path(pid,p['export']['report']).read_text());self.assertEqual(report['shot_count'],2)
  # Confirm original / generated video speech was discarded, not mixed with new voice.
  wav=self.fixtures/'final-audio.wav'
  media.ffmpeg(['-i',server.file_path(pid,p['export']['file']),'-t','2','-ac','1','-ar','16000',wav])
  import array
  with wave.open(str(wav)) as stream:samples=array.array('h',stream.readframes(stream.getnframes()))
  crossings=sum(1 for a,b in zip(samples,samples[1:]) if a<0<=b)
  frequency=crossings/(len(samples)/16000)
  self.assertAlmostEqual(frequency,660,delta=10)
  # Runware Luma edit must carry BOTH the original video and approved frame.
  first=p['shots'][0];sid=first['id'];plan={k:first[k] for k in server.ShotEdit.model_fields}
  self.request('PUT',f'/projects/{pid}/shots/{sid}',json={**plan,'mode':'edit'})
  p=self.job(pid,'video',sid)
  request=next(json.loads(r.content)[0] for r in reversed(self.calls) if r.url.host=='api.runware.ai' and json.loads(r.content)[0].get('model')=='luma:ray@3.2')
  self.assertIn('video',request['inputs']);self.assertEqual(request['inputs']['frameImages'][0]['frame'],'first')
  self.assertEqual(request['settings'],{'edit':{'autoControls':True}})
  # Input changes invalidate export and prevent stale approved results being shipped.
  p=self.request('PUT',f'/projects/{pid}',json={'name':p['name'],'brief':{**p['brief'],'product_name':'Another product'}})
  self.assertIsNone(p['export']);self.assertTrue(all(not s['approved'] and not s['video'] for s in p['shots']))
  # Actual WaveSpeed generation payloads + media download through the same worker.
  p=self.request('PUT',f'/projects/{pid}',json={'name':p['name'],'brief':{**p['brief'],'frame_provider':'wavespeed','video_provider':'wavespeed','voice_provider':'wavespeed','lipsync_provider':'wavespeed'}})
  sid=p['shots'][0]['id'];p=self.job(pid,'frame',sid)
  self.request('POST',f'/projects/{pid}/shots/{sid}/approve/frame',json={'approved':True})
  p=self.job(pid,'video',sid);p=self.job(pid,'voice',sid)
  self.assertTrue(p['shots'][0]['voice'])

 def test_recover_existing_task_does_not_resubmit(self):
  p=self.create_reference();pid=p['id'];sid=p['shots'][0]['id']
  task_id=str(uuid.uuid4());self.remote[task_id]='frame.jpg'
  job={'id':server.uid(),'stage':'frame','shot_id':sid,'status':'interrupted','created':server.now(),
       'revision':p['revision'],'task_id':task_id,'provider':'runware','error':'Simulated restart'}
  server.mutate(pid,lambda p:p['jobs'].append(job))
  count=self.submit_count
  self.request('POST',f'/projects/{pid}/jobs/{job["id"]}/resume',json={})
  deadline=time.time()+30
  while time.time()<deadline:
   p=self.get(pid);result=next(j for j in p['jobs'] if j['id']==job['id'])
   if result['status'] not in server.ACTIVE:break
   time.sleep(.05)
  self.assertEqual(result['status'],'succeeded',result)
  self.assertEqual(self.submit_count,count)
  self.assertIsNotNone(p['shots'][0]['frame'])

 def test_script_change_keeps_approved_visuals_but_invalidates_voice(self):
  p=self.create_reference();pid=p['id'];sid=p['shots'][0]['id']
  def prepare(p):
   s=p['shots'][0];s.update(frame='frame.jpg',frame_approved=True,video='video.mp4',voice='voice.wav',lipsync='synced.mp4',approved=True)
   p['export']={'file':'old.mp4'}
  server.mutate(pid,prepare)
  original=server.ShotEdit().model_dump()
  p=self.request('PUT',f'/projects/{pid}/shots/{sid}',json={**original,'script':'New product wording'})
  shot=p['shots'][0]
  self.assertEqual(shot['video'],'video.mp4');self.assertTrue(shot['frame_approved'])
  self.assertIsNone(shot['voice']);self.assertIsNone(shot['lipsync']);self.assertFalse(shot['approved']);self.assertIsNone(p['export'])

 def test_visual_analysis_requires_coverage_and_does_not_invent_dialogue(self):
  p=self.create_reference();pid=p['id'];revision=p['revision'];old_model=self.providers.vision_model
  self.providers.vision_model='test-vision-model'
  try:
   with patch.object(self.providers,'vision',return_value={'shots':[]}):
    job=self.request('POST',f'/projects/{pid}/jobs',json={'stage':'analyze'})
    deadline=time.time()+15
    while time.time()<deadline:
     p=self.get(pid);result=next(j for j in p['jobs'] if j['id']==job['id'])
     if result['status'] not in server.ACTIVE:break
     time.sleep(.05)
   self.assertEqual(result['status'],'failed');self.assertIn('did not cover every shot',result['error'])
   self.assertEqual(p['revision'],revision);self.assertTrue(all(not s['description'] for s in p['shots']))
   plan={'shots':[{'id':s['id'],'description':'A product is raised toward the camera.','replacement_script':'Invented words from a silent frame.'} for s in p['shots']]}
   with patch.object(self.providers,'vision',return_value=plan):p=self.job(pid,'analyze')
   self.assertTrue(all(s['description'] for s in p['shots']))
   self.assertTrue(all(not s['script'] for s in p['shots']))
  finally:self.providers.vision_model=old_model

 def test_legacy_provider_migration_preserves_media_and_history(self):
  p=self.create_reference();pid=p['id'];source=p['reference']['file']
  p['brief'].update(frame_provider='runway',video_provider='runway',voice_provider='runway',runway_voice='Maya')
  p['brief'].pop('runware_voice');p['brief'].pop('lipsync_provider')
  p['shots'][0]['audio_mode']='convert'
  old_job={'id':server.uid(),'stage':'video','shot_id':p['shots'][0]['id'],'provider':'runway','task_id':'old-provider-id',
    'status':'interrupted','revision':p['revision'],'created':server.now()}
  p['jobs'].append(old_job);server.write(p)
  migrated=self.get(pid)
  self.assertEqual(migrated['brief']['frame_provider'],'runware');self.assertEqual(migrated['brief']['runware_voice'],'Sarah')
  self.assertNotIn('runway_voice',migrated['brief']);self.assertEqual(migrated['brief']['lipsync_provider'],'wavespeed')
  self.assertEqual(migrated['reference']['file'],source);self.assertTrue(server.file_path(pid,source).exists())
  self.assertEqual(migrated['shots'][0]['audio_mode'],'revoice')
  history=next(j for j in migrated['jobs'] if j['id']==old_job['id']);self.assertEqual(history['provider'],'runway');self.assertEqual(history['task_id'],'old-provider-id')
  self.assertEqual(self.client.post(f'/api/projects/{pid}/jobs/{old_job["id"]}/resume',headers=HEADERS,json={}).status_code,409)
  self.assertEqual(self.get(pid)['revision'],migrated['revision'])

 def test_unknown_runware_submission_retains_preselected_uuid(self):
  p=self.create_reference();pid=p['id'];sid=p['shots'][0]['id']
  self.request('PUT',f'/projects/{pid}',json={'name':p['name'],'brief':{**p['brief'],'product_name':'Our bottle'}})
  with (self.fixtures/'frame.jpg').open('rb') as f:self.request('POST',f'/projects/{pid}/assets/product',files={'file':('bottle.jpg',f,'image/jpeg')})
  self.request('PUT',f'/projects/{pid}/shots/{sid}',json=server.ShotEdit(description='Presenter holds a bottle.').model_dump())
  with patch.object(self.providers,'submit',side_effect=SubmissionUnknown('Simulated connection loss after submission')):
   job=self.request('POST',f'/projects/{pid}/jobs',json={'stage':'frame','shot_id':sid})
   deadline=time.time()+15
   while time.time()<deadline:
    p=self.get(pid);result=next(j for j in p['jobs'] if j['id']==job['id'])
    if result['status'] not in server.ACTIVE:break
    time.sleep(.05)
  self.assertEqual(result['status'],'unknown');self.assertEqual(result['provider'],'runware')
  self.assertEqual(result['task_id'],str(uuid.UUID(job['id'])))
  blocked=self.client.post(f'/api/projects/{pid}/jobs',headers=HEADERS,json={'stage':'frame','shot_id':sid})
  self.assertEqual(blocked.status_code,409)

 def test_assembly_handles_spaces_quotes_and_unicode_paths(self):
  directory=self.fixtures/"Windows user's folder"/'منتج'
  directory.mkdir(parents=True,exist_ok=True)
  clip=directory/'a quoted clip.mp4'
  media.normalize_clip(self.fixtures/'replacement.mp4',clip,1,(180,320),self.fixtures/'voice.wav')
  destination=directory/'final ad.mp4'
  media.assemble([clip,clip],destination,durations=[1,1])
  info=media.probe(destination)
  self.assertTrue(info['has_audio']);self.assertAlmostEqual(info['duration'],2,delta=.15)
  self.assertIn("'\\''",(directory/'concat.txt').read_text(encoding='utf-8'))

 def test_media_rejects_overlong_speech(self):
  with self.assertRaisesRegex(media.MediaError,'Shorten the script'):media.fit_voice(self.fixtures/'too-long.wav',self.fixtures/'fitted.wav',4)

 def test_api_security_missing_shots_and_isolation(self):
  self.assertEqual(self.client.post('/api/projects',json={'name':'forbidden'}).status_code,403)
  self.assertEqual(self.client.post('/api/projects',headers={**HEADERS,'Origin':'https://evil.example'},json={'name':'forbidden'}).status_code,403)
  self.assertEqual(self.client.get('/.data/project.json').status_code,404)
  self.assertEqual(self.client.get('/backend/server.py').status_code,404)
  self.assertEqual(self.client.get('/api/projects/not-a-uuid').status_code,404)
  p=self.create_reference();pid=p['id']
  bad=self.client.post(f'/api/projects/{pid}/jobs',headers=HEADERS,json={'stage':'assemble'})
  self.assertEqual(bad.status_code,422)
  bad=self.client.post(f'/api/projects/{pid}/cuts',headers=HEADERS,json={'boundaries':[0,5,4,8]})
  self.assertEqual(bad.status_code,422)
  # Genuine asset format validation rather than extension-only acceptance.
  bad=self.client.post(f'/api/projects/{pid}/assets/product',headers=HEADERS,files={'file':('fake.jpg',b'not an image','image/jpeg')})
  self.assertEqual(bad.status_code,422)
  data=self.client.get('/api/health').text
  self.assertNotIn('test-not-a-real-key',data)

if __name__=='__main__':unittest.main()

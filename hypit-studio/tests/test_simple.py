"""The three-step flow, with mocked Runware and real FFmpeg exports."""
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from test_studio import runware_schema
from backend import media, server, simple
from backend.providers import Providers, SubmissionUnknown


class SimpleFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='hypit-simple-')
        cls.folder = Path(cls.temp.name)
        cls.fixtures = cls.folder/'fixtures'
        cls.fixtures.mkdir()
        cls.data = cls.folder/'data'
        cls.data.mkdir()
        media.ffmpeg(['-f','lavfi','-i','color=c=red:s=180x320:r=30:d=2',
                      '-f','lavfi','-i','color=c=blue:s=180x320:r=30:d=2',
                      '-f','lavfi','-i','sine=frequency=440:duration=4',
                      '-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]',
                      '-map','[v]','-map','2:a','-c:v','libx264','-preset','ultrafast',
                      '-c:a','aac','-shortest',cls.fixtures/'reference.mp4'])
        media.ffmpeg(['-f','lavfi','-i','color=c=green:s=180x320:r=30:d=2',
                      '-c:v','libx264','-preset','ultrafast',cls.fixtures/'video.mp4'])
        media.ffmpeg(['-f','lavfi','-i','sine=frequency=660:duration=1',cls.fixtures/'voice.wav'])
        Image.new('RGB',(180,320),'green').save(cls.fixtures/'frame.jpg')
        cls.calls=[]
        cls.remote={}
        cls.bad_script=False
        cls.bad_analysis=False
        cls.provider=Providers(httpx.Client(transport=httpx.MockTransport(cls.mock)))
        cls.provider.keys['runware']='fake-test-key'
        cls.provider.public_url=lambda url:url
        cls.old_data,cls.old_provider=server.DATA,server.PROVIDERS
        server.DATA,server.PROVIDERS=cls.data,cls.provider
        cls.context=TestClient(server.app)
        cls.client=cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None,None,None)
        server.DATA,server.PROVIDERS=cls.old_data,cls.old_provider
        cls.temp.cleanup()

    @classmethod
    def mock(cls,req):
        cls.calls.append(req)
        if req.url.host=='schemas.runware.ai':
            from urllib.parse import unquote
            model=unquote(req.url.path.removeprefix('/resolve/'))
            if model==simple.ANALYST:
                schema={'type':'object','properties':{
                    'taskType':{'const':'textInference'},'taskUUID':{'type':'string'},'model':{'const':model},
                    'deliveryMethod':{'const':'async'},'includeCost':{'type':'boolean'},'numberResults':{'const':1},
                    'messages':{'type':'array','minItems':1},'settings':{'type':'object'},'inputs':{'type':'object'}},
                    'required':['taskType','taskUUID','model','deliveryMethod','messages','settings'], 'additionalProperties':False}
            else:schema=runware_schema(model)
            return httpx.Response(200,json={'requestSchema':schema})
        if req.url.host=='assets.example.test':
            return httpx.Response(200,content=(cls.fixtures/req.url.path.rsplit('/',1)[1]).read_bytes())
        task=json.loads(req.content)[0]
        tid=task['taskUUID'];kind=task['taskType']
        if kind=='mediaStorage':
            data={'taskUUID':tid,'mediaUUID':str(uuid.uuid4())}
        elif kind=='ping':
            data={'taskUUID':tid,'pong':True}
        elif kind=='getResponse':
            result=cls.remote[tid]
            if isinstance(result,dict):data={'taskUUID':tid,'status':'success','text':json.dumps(result)}
            else:
                key='imageURL' if result=='frame.jpg' else 'audioURL' if result=='voice.wav' else 'videoURL'
                data={'taskUUID':tid,'status':'success',key:'https://assets.example.test/'+result}
        elif kind=='textInference':
            prompt=task['messages'][0]['content']
            if prompt.startswith('Analyze'):
                shots=json.loads(prompt.split('Source shot ranges: ')[1])
                result={'summary':'A presenter shows a product at a table.',
                        'keyframes':[{'timestamp':t,'description':'Presenter and table'} for t in [.1,.8,1.6,2.6,3.7]],
                        'shots':[{'id':s['id'],'description':'Presenter holds a bottle at a table.',
                                  'source_dialogue':'Hello.' if i==0 else 'World.','talking':True} for i,s in enumerate(shots)]}
                if cls.bad_analysis:result['shots']=result['shots'][:1]
            else:
                payload=json.loads(prompt[prompt.index('{"changes":'):])
                words=payload['script'].split() if payload['script'] else []
                result={'shots':[{'id':s['id'],'changes':payload['changes'], 'mode':'edit',
                                  'script':(' '.join(words[:len(words)//2] if i==0 else words[len(words)//2:]) if words else s['source_dialogue'])
                                  if payload['audio']=='new' else ''} for i,s in enumerate(payload['shots'])]}
                if cls.bad_script:result['shots'][0]['script']='Invented claim.'
            cls.remote[tid]=result
            data={'taskUUID':tid,'status':'processing'}
        else:
            cls.remote[tid]='frame.jpg' if kind=='imageInference' else 'voice.wav' if kind=='audioInference' else 'video.mp4'
            data={'taskUUID':tid,'status':'processing'}
        return httpx.Response(200,json={'data':[data]})

    def request(self,method,path,**kwargs):
        response=self.client.request(method,'/api'+path,headers={'X-Hypit-Client':'studio'},**kwargs)
        self.assertLess(response.status_code,300,response.text)
        return response.json()

    def create(self):
        p=self.request('POST','/projects',json={'name':'Simple workflow test'})
        with (self.fixtures/'reference.mp4').open('rb') as f:
            p=self.request('POST',f'/projects/{p["id"]}/simple/reference',files={'file':('reference.mp4',f,'video/mp4')})
        self.assertEqual(len(p['simple']['keyframes']),5)
        return p

    def get(self,pid):return self.request('GET',f'/projects/{pid}')

    def wait(self,pid,action,success=True):
        job=self.request('POST',f'/projects/{pid}/simple/start',json={'action':action})
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            p=self.get(pid)
            row=next(j for j in p['jobs'] if j['id']==job['id'])
            if row['status'] not in server.ACTIVE:
                self.assertEqual(row['status'],'succeeded' if success else 'failed',row)
                return p
            time.sleep(.05)
        self.fail('Workflow did not finish: '+action)

    def recipe(self,p,**values):
        recipe={key:p['simple'][key] for key in simple.Recipe.model_fields}
        return self.request('PUT',f'/projects/{p["id"]}/simple',json={**recipe,**values})

    def test_blue_table_needs_only_a_prompt_and_exports_without_manual_shot_work(self):
        start=len(self.calls)
        p=self.create();pid=p['id']
        p=self.wait(pid,'analyze')
        self.assertTrue(p['simple']['analysis_ready'])
        self.assertEqual(p['simple']['source_script'],'Hello. World.')
        p=self.recipe(p,prompt='Make the table blue. Keep everything else the same.')
        p=self.wait(pid,'frames')
        self.assertTrue(all(f['edited'] for f in p['simple']['keyframes']))
        self.assertFalse(p['assets'])
        p=self.wait(pid,'render')
        self.assertIsNotNone(p['export'])
        self.assertAlmostEqual(media.probe(server.file_path(pid,p['export']['file']))['duration'],4,delta=.15)
        self.assertTrue(all(s['audio_mode']=='original' for s in p['shots']))
        self.assertTrue(all(s['video'] and not s['approved'] for s in p['shots']))
        report=json.loads(server.file_path(pid,p['export']['report']).read_text())
        self.assertIn('AI draft',report['quality_review'])
        requests=[r for r in self.calls[start:] if r.url.host=='api.runware.ai']
        tasks=[json.loads(r.content)[0] for r in requests]
        analysis=next(t for t in tasks if t['taskType']=='textInference' and t.get('inputs'))
        self.assertTrue(analysis['inputs']['videos'])
        self.assertFalse(any(r.url.host=='api.wavespeed.ai' for r in self.calls[start:]))
        self.assertEqual(sum(t['taskType']=='imageInference' for t in tasks),7)
        count=len(tasks)
        p=self.wait(pid,'render')
        self.assertEqual(len([r for r in self.calls[start:] if r.url.host=='api.runware.ai']),count)
        # A per-frame correction keeps the other four approved visual anchors.
        p=self.recipe(p,notes={p['simple']['keyframes'][2]['id']:'Remove the cup.'})
        self.assertEqual(sum(bool(f['edited']) for f in p['simple']['keyframes']),4)
        self.assertIsNone(p['export'])
        self.assertTrue(all(not s['video'] for s in p['shots']))
        self.assertIn('Remove the cup.',simple.frame_prompt(p,p['shots'][0]))
        self.assertIn('Remove the cup.',server.video_prompt(p,p['shots'][0]))
        self.assertNotIn('Remove the cup.',simple.frame_prompt(p,p['shots'][1]))

    def test_new_script_generates_voice_and_lipsync_automatically(self):
        p=self.wait(self.create()['id'],'analyze');pid=p['id']
        p=self.recipe(p,prompt='Use my new character.',audio='new',script='Hello new person. Welcome to my room.')
        with (self.fixtures/'frame.jpg').open('rb') as f:
            p=self.request('POST',f'/projects/{pid}/simple/assets/character',files={'file':('cast.jpg',f,'image/jpeg')})
        p=self.wait(pid,'frames')
        frames=[f['edited'] for f in p['simple']['keyframes']]
        p=self.wait(pid,'render')
        self.assertTrue(all(s['voice'] and s['lipsync'] for s in p['shots']))
        self.assertTrue(p['export'])
        self.assertEqual(' '.join(s['script'] for s in p['shots']),p['simple']['script'])
        p=self.recipe(p,speech='narration')
        self.assertEqual([f['edited'] for f in p['simple']['keyframes']],frames)
        p=self.wait(pid,'frames')
        self.assertTrue(all(not s['talking'] for s in p['shots']))
        p=self.recipe(p,speech='auto')
        p=self.wait(pid,'frames')
        self.assertTrue(all(s['talking'] for s in p['shots']))

    def test_invalid_analysis_and_changed_script_never_apply_partial_results(self):
        p=self.create();pid=p['id']
        type(self).bad_analysis=True
        try:p=self.wait(pid,'analyze',success=False)
        finally:type(self).bad_analysis=False
        self.assertFalse(p['simple']['analysis_ready'])
        self.assertTrue(all(not s['description'] for s in p['shots']))
        # The same paid task must be dismissed explicitly before a fresh analysis.
        child=next(j for j in reversed(p['jobs']) if j.get('slot')=='simple-analysis')
        self.request('POST',f'/projects/{pid}/jobs/{child["id"]}/dismiss',json={})
        p=self.wait(pid,'analyze')
        p=self.recipe(p,prompt='Make table blue.',audio='new',script='Keep these exact words please.')
        type(self).bad_script=True
        try:p=self.wait(pid,'frames',success=False)
        finally:type(self).bad_script=False
        self.assertFalse(p['simple']['plan_ready'])
        self.assertTrue(all(not f['edited'] for f in p['simple']['keyframes']))

    def test_unknown_submission_continues_same_uuid_without_a_second_paid_post(self):
        p=self.create();pid=p['id'];seen=[]
        prepare=lambda: ('google:4@3','imageInference',{'positivePrompt':'Blue table','resolution':'1K',
                             'inputs':{'referenceImages':['fixture']},'outputType':'URL'})
        with patch.object(self.provider,'submit',side_effect=SubmissionUnknown('Timed out.')) as submit:
            with self.assertRaises(SubmissionUnknown):simple.operation(pid,'pipeline','test-unknown','simple-keyframe',prepare,lambda *args:None)
            self.assertEqual(submit.call_count,1)
        before=self.get(pid)['jobs'][-1]
        self.assertEqual(before['status'],'unknown')
        with patch.object(self.provider,'submit') as submit,patch.object(self.provider,'poll',return_value={'done':True,'text':'saved result'}) as poll:
            simple.operation(pid,'pipeline','test-unknown','simple-keyframe',prepare,lambda text,jid:seen.append(text))
            submit.assert_not_called();poll.assert_called_once_with('runware',before['task_id'])
        self.assertEqual(seen,['saved result'])
        self.assertEqual(self.get(pid)['jobs'][-1]['status'],'succeeded')

    def test_missing_key_and_nonfinite_or_stale_inputs_are_blocked(self):
        p=self.create();pid=p['id']
        with patch.object(self.provider,'keys',{}):
            response=self.client.post(f'/api/projects/{pid}/simple/start',headers={'X-Hypit-Client':'studio'},json={'action':'analyze'})
            self.assertEqual(response.status_code,422)
        response=self.client.post(f'/api/projects/{pid}/simple/start',headers={'X-Hypit-Client':'studio'},json={'action':'render'})
        self.assertEqual(response.status_code,422)
        with self.assertRaises(ValueError):simple.FrameResult(timestamp=float('nan'),description='Invalid')
        self.assertEqual(self.client.get('/simple.js').status_code,200)
        self.assertEqual(self.client.get('/simple.css').status_code,200)


if __name__=='__main__':unittest.main()

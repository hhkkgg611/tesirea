"""Provider contracts from the official Runware SDK and WaveSpeed SDK/catalog.
No automatic retries for paid POSTs. Credentials are never sent to asset hosts.
"""
from __future__ import annotations
import base64
import ipaddress
import json
import mimetypes
import os
import re
import socket
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, quote
import httpx
from jsonschema import Draft7Validator
from jsonschema.validators import validator_for

RUNWARE = 'https://api.runware.ai'
RUNWARE_SCHEMAS = 'https://schemas.runware.ai/resolve/'
WAVESPEED = 'https://api.wavespeed.ai'
LLM = 'https://llm.wavespeed.ai/v1'

class ProviderError(ValueError):
    pass

class SubmissionUnknown(ProviderError):
    """The request might have created a paid task. Never resubmit it automatically."""

class Providers:
    def __init__(self, client=None):
        self.keys = {}
        self.vision_model = os.getenv('WAVESPEED_VISION_MODEL', '')
        self.client = client or httpx.Client(timeout=httpx.Timeout(90, connect=20), follow_redirects=False)
        self.catalog_cache = None
        self.runware_schema_cache = {}

    def key(self, provider):
        names = {'runware': ('RUNWARE_API_KEY',), 'wavespeed': ('WAVESPEED_API_KEY',)}
        if provider not in names:
            raise ProviderError('Unsupported provider. Legacy tasks cannot be polled through a different provider.')
        value = self.keys.get(provider) or next((os.getenv(n) for n in names[provider] if os.getenv(n)), None)
        if not value:
            raise ProviderError(f'Connect {provider.title()} in Connections before generating.')
        return value

    def configured(self, provider):
        try:
            self.key(provider)
            return True
        except ProviderError:
            return False

    def headers(self, provider):
        return {'Authorization': f'Bearer {self.key(provider)}'}

    def safe_error(self, text):
        for provider in ['runware', 'wavespeed']:
            if self.configured(provider):
                text = text.replace(self.key(provider), '[redacted]')
        return re.sub(r'https?://[^\s"\']+', '[provider URL]', text)[:500]

    def request(self, provider, method, path, body=None, *, llm=False, paid=False):
        base = LLM if llm else RUNWARE if provider == 'runware' else WAVESPEED
        try:
            response = self.client.request(method, base+path, headers=self.headers(provider), json=body)
        except httpx.TransportError as exc:
            if paid:
                raise SubmissionUnknown('Submission outcome unknown. The provider may have billed this request. Check its dashboard before submitting again; the app will not retry it.') from exc
            raise ProviderError('Cannot reach provider through the configured network. Check the destination allowlist and connectivity.') from exc
        if not 200 <= response.status_code < 300:
            detail = ''
            try:
                data = response.json()
                error = data.get('errors') or data.get('error', data.get('message', ''))
                detail = self.safe_error(str(error))
            except (ValueError, TypeError):
                pass
            # A server-side failure on a paid submit is ambiguous too.
            if paid and response.status_code >= 500:
                raise SubmissionUnknown('Provider returned a server error while submitting. Check the provider dashboard before retrying; a task may already exist.')
            raise ProviderError(f'{provider.title()} returned HTTP {response.status_code}. {detail or "Check credentials, account credits, model access, and network policy."}')
        try:
            data = response.json()
        except ValueError as exc:
            error = SubmissionUnknown if paid else ProviderError
            raise error('Provider returned an unreadable response. Check the provider dashboard before retrying a submission.') from exc
        if not isinstance(data,dict):
            error=SubmissionUnknown if paid else ProviderError
            raise error('Provider returned an invalid response envelope. Poll the recorded task ID before retrying a paid submission.')
        if provider == 'runware' and data.get('errors'):
            errors=data['errors']
            message='; '.join(str(e.get('message') or e.get('code') or 'Runware rejected the request.') for e in errors if isinstance(e,dict))
            raise ProviderError(self.safe_error(message or 'Runware rejected the request.'))
        if provider == 'wavespeed' and not llm:
            if data.get('code', 200) != 200:
                raise ProviderError(self.safe_error(str(data.get('message', 'WaveSpeed rejected the request.'))))
            return data.get('data', data)
        return data

    @staticmethod
    def public_url(url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ProviderError('Provider asset URL must be public HTTPS on port 443.')
        host = parsed.hostname.lower()
        if host in {'localhost','proxy'} or host.endswith(('.local','.internal','.localhost')):
            raise ProviderError('Private asset destinations are not permitted.')
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                addresses = [ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)]
            except socket.gaierror as exc:
                raise ProviderError(f'Cannot resolve provider asset host {host}. Check the network settings.') from exc
        if not addresses or any(not a.is_global for a in addresses):
            raise ProviderError('Private asset destinations are not permitted.')
        return url

    def unsigned_request(self, method, url, **kwargs):
        # Signed storage URLs are validated; never inherit the API's bearer header.
        try:
            response = self.client.request(method, self.public_url(url), **kwargs)
        except httpx.TransportError as exc:
            raise ProviderError(f'Provider asset transfer failed. Allow {urlparse(url).hostname} in network settings, then resume this task.') from exc
        if not 200 <= response.status_code < 300:
            raise ProviderError(f'Provider asset host {urlparse(url).hostname} returned HTTP {response.status_code}. Check network access or signed-URL expiry.')
        return response

    def upload(self, provider, path: Path):
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        if provider == 'runware':
            if path.stat().st_size > 30*1024*1024:
                raise ProviderError('Runware upload exceeds this adapter’s 30MB inline-media limit. Use a smaller reference clip or image.')
            # Official mediaStorage contract uses raw base64, not a signed upload ticket.
            media_data=base64.b64encode(path.read_bytes()).decode()
            task_id=str(uuid.uuid4())
            data=self.runware_task({'taskType':'mediaStorage','taskUUID':task_id,'operation':'upload','media':media_data})
            identifier=data.get('mediaUUID')
            if not identifier:raise ProviderError('Runware upload returned no mediaUUID.')
            return identifier
        ticket = self.request(provider, 'POST', '/api/v3/media/uploads', {'filename': path.name, 'size': path.stat().st_size, 'content_type': mime})
        instruction = ticket.get('upload', {})
        if instruction.get('method','').upper() != 'PUT':
            raise ProviderError('Unsupported WaveSpeed upload contract. Update the adapter before generating.')
        with path.open('rb') as stream:
            self.unsigned_request('PUT', instruction['url'], headers=instruction.get('headers', {}), content=stream)
        return ticket['download_url']

    def catalog(self, refresh=False):
        if refresh or not self.catalog_cache or time.time()-self.catalog_cache[0] > 3600:
            result = self.request('wavespeed','GET','/api/v3/models')
            if not isinstance(result,list):
                raise ProviderError('WaveSpeed returned an unsupported model catalog.')
            self.catalog_cache = (time.time(), result)
        return self.catalog_cache[1]

    def schema(self, model):
        entry = next((m for m in self.catalog() if m.get('model_id') == model), None)
        if not entry:
            raise ProviderError(f'{model} is unavailable in the live WaveSpeed catalog. No substitute was submitted.')
        schemas = entry.get('api_schema', {}).get('api_schemas', [])
        schema = next((s.get('request_schema') for s in schemas if s.get('request_schema', {}).get('properties')), None)
        if not schema:
            raise ProviderError(f'{model} has no usable request schema. Generation was not submitted.')
        return schema

    def find_model(self, purpose):
        if purpose == 'lipsync':
            preferred = ['sync/lipsync-2-pro', 'sync/lipsync-2']
            entries = self.catalog()
            candidates = [m for m in entries if 'lip' in m.get('model_id','').lower()]
            candidates.sort(key=lambda m: preferred.index(m['model_id']) if m['model_id'] in preferred else len(preferred))
            for m in candidates:
                props = self.schema(m['model_id']).get('properties',{})
                if 'video' in props and 'audio' in props:
                    return m['model_id']
            raise ProviderError('No video + audio lip-sync model was found in the live WaveSpeed catalog. Upload a lip-synced take, or select voiceover for off-screen speech.')
        if purpose == 'transcribe':
            for m in self.catalog():
                if 'whisper' in m.get('model_id','').lower() and 'audio' in self.schema(m['model_id']).get('properties',{}):
                    return m['model_id']
            raise ProviderError('No compatible Whisper transcription endpoint is available. Enter the transcript per shot.')
        raise ProviderError('Unsupported model purpose.')

    def validate_wave(self, model, payload):
        schema = self.schema(model)
        # Fill only required defaults explicitly declared by the live provider.
        for name in schema.get('required',[]):
            definition=schema.get('properties',{}).get(name,{})
            if name not in payload and 'default' in definition:payload[name]=definition['default']
        # Do not silently discard character, product, audio, or reference inputs.
        unknown = set(payload)-set(schema.get('properties',{}))
        if unknown:
            raise ProviderError(f'{model} no longer accepts {", ".join(sorted(unknown))}. No request was submitted. Update the adapter.')
        errors = sorted(Draft7Validator(schema).iter_errors(payload), key=lambda e: str(e.path))
        if errors:
            err=errors[0]
            field='.'.join(str(x) for x in err.path) or 'request'
            # Validation messages may contain signed URLs; never echo the instance.
            raise ProviderError(f'{model}: {field} violates the live schema ({err.validator}). No request was submitted.')
        return payload

    def runware_task(self, task, *, paid=False):
        envelope=self.request('runware','POST','/v1',[task],paid=paid)
        items=envelope.get('data',[])
        if not isinstance(items,list):
            error=SubmissionUnknown if paid else ProviderError
            raise error('Runware returned an invalid data envelope. Poll the recorded task ID before retrying generation.')
        # A single-item batch can only return this one task. An explicit mismatch
        # is never accepted, including an unrelated completed output.
        result=next((item for item in items if isinstance(item,dict) and item.get('taskUUID')==task['taskUUID']),None)
        if result is None and task['taskType']=='ping':
            result=next((item for item in items if isinstance(item,dict) and item.get('taskType')=='ping'),None)
        if result is None:
            error=SubmissionUnknown if paid else ProviderError
            raise error('Runware returned no matching taskUUID. Poll the recorded task ID; do not resubmit generation.')
        return result

    def runware_schema(self, model):
        cached=self.runware_schema_cache.get(model)
        if cached and time.time()-cached[0]<3600:return cached[1]
        # Public schema endpoint is intentionally unauthenticated.
        try:
            response=self.client.get(RUNWARE_SCHEMAS+quote(model,safe=''))
            response.raise_for_status()
            schema=response.json()['requestSchema']
            if not isinstance(schema,dict):raise ValueError()
            validator_for(schema).check_schema(schema)
        except (httpx.HTTPError,ValueError,KeyError) as exc:
            raise ProviderError(f'Cannot load the current Runware schema for {model}. Allow schemas.runware.ai and verify model availability; no generation was submitted.') from exc
        self.runware_schema_cache[model]=(time.time(),schema)
        return schema

    def validate_runware(self, model, task):
        schema=self.runware_schema(model)
        try:
            errors=list(validator_for(schema)(schema).iter_errors(task))
        except Exception as exc:
            raise ProviderError('Runware model schema could not be resolved safely. No generation was submitted.') from exc
        if errors:
            error=errors[0];field='.'.join(map(str,error.path)) or 'request'
            raise ProviderError(f'{model}: {field} violates the current Runware schema ({error.validator}). No request was submitted.')
        return task

    def submit(self, provider, model, payload, route=None):
        if provider == 'runware':
            task_id=payload.get('taskUUID')
            if not task_id:raise ProviderError('Runware taskUUID must be persisted before submission.')
            task={'taskType':route,'model':model,'deliveryMethod':'async','includeCost':True,'numberResults':1,**payload}
            self.validate_runware(model,task)
            data=self.runware_task(task,paid=True)
            return {'id':data['taskUUID'],'cost':data.get('cost')}
        self.validate_wave(model,payload)
        data = self.request(provider,'POST','/api/v3/'+model,payload,paid=True)
        if not data.get('id'):
            raise SubmissionUnknown('No task ID was returned. Check the provider dashboard before retrying.')
        return data

    def poll(self, provider, task_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',task_id):
            raise ProviderError('Invalid task ID.')
        if provider=='runware':
            try:uuid.UUID(task_id)
            except ValueError as exc:raise ProviderError('Runware task IDs must be UUIDs. Do not reuse IDs from another provider.') from exc
            data=self.runware_task({'taskType':'getResponse','taskUUID':task_id})
            status=str(data.get('status','')).lower()
            if status in {'failed','error','cancelled','canceled'}:
                raise ProviderError('Runware task '+status+': '+self.safe_error(str(data.get('error') or data.get('message') or status)))
            outputs=[data[k] for k in ('imageURL','videoURL','audioURL','mediaURL') if data.get(k)]
            if outputs:return {'done':True,'outputs':outputs,'cost':data.get('cost')}
            if status in {'success','succeeded','completed'}:raise ProviderError('Runware task completed without an output asset.')
            return {'done':False,'status':status or 'processing','cost':data.get('cost')}
        if provider!='wavespeed':raise ProviderError('Legacy provider task cannot be resumed through Runware or WaveSpeed.')
        data=self.request(provider,'GET','/api/v3/predictions/'+task_id+'/result')
        status=str(data.get('status','')).lower()
        if status in {'succeeded','completed'}:
            outputs=data.get('outputs')
            if not isinstance(outputs,list) or not outputs:
                raise ProviderError('The task completed without any output assets.')
            return {'done':True,'outputs':outputs,'cost':data.get('cost')}
        if status in {'failed','cancelled','canceled'}:
            detail=data.get('failure') or data.get('error') or data.get('failureCode') or status
            raise ProviderError('Provider task '+status+': '+self.safe_error(str(detail)))
        return {'done':False,'status':status,'cost':data.get('estimatedCost')}

    def download(self, url, destination: Path):
        # Stream bounded downloads; follow only validated HTTPS redirects without credentials.
        for _ in range(5):
            try:
                with self.client.stream('GET',self.public_url(url)) as response:
                    if response.status_code in (301,302,303,307,308):
                        from urllib.parse import urljoin
                        url=urljoin(url,response.headers['location']);continue
                    if response.status_code != 200:
                        raise ProviderError(f'Output download returned HTTP {response.status_code}. Resume the task to request a fresh output URL.')
                    size=0
                    with destination.open('wb') as out:
                        for chunk in response.iter_bytes():
                            size+=len(chunk)
                            if size>250*1024*1024:
                                raise ProviderError('Provider output exceeded the 250MB download limit.')
                            out.write(chunk)
                    if not size:
                        raise ProviderError('Provider returned an empty output asset.')
                    return
            except httpx.TransportError as exc:
                raise ProviderError(f'Cannot download provider output. Allow {urlparse(url).hostname} in network settings and resume the task.') from exc
        raise ProviderError('Too many redirects while downloading output.')

    def vision(self, shots, replacements):
        if not self.vision_model:
            raise ProviderError('Choose a vision-capable WaveSpeed LLM model in Connections, or describe shots manually.')
        content=[{'type':'text','text':
            'Analyze these ordered reference-ad frames. Frame labels give shot IDs. Return ONLY JSON: {"shots":[{"id":"...","description":"visible actions, framing and camera motion","replacement_script":"..."}]}. '
            'Do not invent spoken words from images. Only rewrite dialogue if source_dialogue is supplied. Otherwise return empty replacement_script. '
            'Preserve the shot purpose and pacing. Use only supplied product facts, no invented claims. Replacement brief: '+json.dumps(replacements)}]
        for shot in shots:
            content.append({'type':'text','text':json.dumps({k:shot[k] for k in ('id','duration','source_dialogue')})})
            for path in shot['frames']:
                with path.open('rb') as f:
                    encoded=base64.b64encode(f.read()).decode()
                content.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+encoded}})
        data=self.request('wavespeed','POST','/chat/completions',{'model':self.vision_model,'stream':False,'max_tokens':6000,
            'messages':[{'role':'user','content':content}]},llm=True,paid=True)
        try:
            text=data['choices'][0]['message']['content']
            text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
            result=json.loads(text)
            if not isinstance(result.get('shots'),list):
                raise ValueError()
            return result
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise ProviderError('Vision model returned an invalid shot breakdown. No generation was started; retry analysis or fill in descriptions manually.') from exc

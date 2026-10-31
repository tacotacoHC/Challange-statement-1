"""Entry-only sharing configuration and retry-safe remote intake (stdlib only)."""
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

SETTINGS_COLLECTION = 'remoteEntrySettings'
SETTINGS_ID = 'workspace'
ENTRY_FIELDS = {'projectId','location','date','time','contributor','type','text','caption','transcript','discipline','files'}
ZONE = ZoneInfo('Asia/Singapore')

class RemoteEntryError(ValueError):
    def __init__(self,message,status=400):
        super().__init__(message)
        self.message=message
        self.status=status

def timestamp(): return dt.datetime.now(ZONE).isoformat(timespec='seconds')

def settings(store):
    default={'id':SETTINGS_ID,'projectId':'workspace','enabled':True,'accessCode':secrets.token_urlsafe(24),'publicBaseUrl':'','receiverPort':8001,'createdAt':timestamp(),'updatedAt':timestamp()}
    # INSERT OR IGNORE keeps the same first code even when both local processes start together.
    with store.connect() as con:
        con.execute('INSERT OR IGNORE INTO documents(collection,id,body) VALUES(?,?,?)',(SETTINGS_COLLECTION,SETTINGS_ID,json.dumps(default)))
        row=con.execute('SELECT body FROM documents WHERE collection=? AND id=?',(SETTINGS_COLLECTION,SETTINGS_ID)).fetchone()
    return json.loads(row['body'])

def save_settings(workspace,payload):
    if set(payload)-{'projectId','enabled','publicBaseUrl','rotateCode'}:raise RemoteEntryError('Unsupported entry-sharing setting.')
    config=settings(workspace.store)
    if 'projectId' in payload:
        projectid=str(payload['projectId']).strip()
        workspace.project(projectid)
        if config['projectId']!=projectid:config['accessCode']=secrets.token_urlsafe(24)
        config['projectId']=projectid
    if 'enabled' in payload:
        if not isinstance(payload['enabled'],bool):raise RemoteEntryError('Choose whether entry intake is enabled.')
        config['enabled']=payload['enabled']
    if 'publicBaseUrl' in payload:
        address=str(payload['publicBaseUrl'] or '').strip().rstrip('/')
        if address:
            parsed=urllib.parse.urlsplit(address)
            local=parsed.hostname in {'127.0.0.1','localhost','::1'}
            if parsed.scheme not in ({'http','https'} if local else {'https'}) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise RemoteEntryError('Enter the HTTPS ngrok URL, without a password, query or access code.')
            if parsed.path not in ('','/'):raise RemoteEntryError('Enter only the ngrok base URL, without /submit.')
        config['publicBaseUrl']=address
    if payload.get('rotateCode') is True:config['accessCode']=secrets.token_urlsafe(24)
    config['updatedAt']=timestamp()
    workspace.store.put(SETTINGS_COLLECTION,config)
    return config

def authorize(store,authorization):
    config=settings(store)
    supplied=authorization[7:].strip() if str(authorization).startswith('Bearer ') else ''
    if not supplied or len(supplied)>256 or not hmac.compare_digest(supplied.encode('utf-8'),config['accessCode'].encode('utf-8')):
        raise RemoteEntryError('Enter the entry access code shared by your site officer.',401)
    if not config.get('enabled'):raise RemoteEntryError('Remote entries are paused. Contact the site officer or try again later.',503)
    return config

def entry_context(workspace,config):
    project=workspace.project(config['projectId'])
    current=dt.datetime.now(ZONE)
    return {'project':{'id':project['id'],'name':project['name']},'today':current.date().isoformat(),'time':current.strftime('%H:%M'),'timezone':'Asia/Singapore','limits':{'imageMB':12,'audioMB':25,'totalMB':34,'files':6},'notice':'Entries are saved for officer review. Photos and recordings use supplied captions and transcripts; no automatic media assessment happens during remote submission.'}

def submit_entry(workspace,payload,config,request_key,source=None):
    if not isinstance(payload,dict):raise RemoteEntryError('The entry must be a JSON object.')
    if set(payload)-ENTRY_FIELDS:raise RemoteEntryError('Unsupported entry fields: '+', '.join(sorted(set(payload)-ENTRY_FIELDS)))
    if not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}',request_key):raise RemoteEntryError('Supply an Idempotency-Key of 8 to 128 letters, numbers, dots, colons, dashes or underscores.')
    for field,value in payload.items():
        if field!='files' and not isinstance(value,str):raise RemoteEntryError('Entry fields must be text values.')
    projectid=payload.get('projectId') or config['projectId']
    if projectid!=config['projectId']:raise RemoteEntryError('This link is configured for a different project. Reconnect to get the current project.',403)
    record={**payload,'projectId':projectid}
    if 'files' in record:
        if not isinstance(record['files'],list) or any(not isinstance(f,dict) or set(f)-{'name','mime','data'} or any(not isinstance(v,str) for v in f.values()) for f in record['files']):
            raise RemoteEntryError('Files must contain a name, MIME type and base64 data.')
    fingerprint_data={'entry':record,'source':source} if source else record
    fingerprint=hashlib.sha256(json.dumps(fingerprint_data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    receiptid='remote_'+hashlib.sha256((projectid+':'+request_key).encode()).hexdigest()
    with workspace.store.lock:
        existing=workspace.store.get('remoteEntryReceipts',receiptid)
        if existing:
            if existing['fingerprint']!=fingerprint:raise RemoteEntryError('This submission reference was already used for different content. Start a new entry to submit changed information.',409)
            return {**existing['response'],'duplicate':True},200
        update=workspace.save_update(record,process_media=False,return_update=True)
        update.update({'submissionOrigin':'Remote entry','receivedAt':timestamp(),'requiresOfficerReview':True})
        if source:
            update['submissionOrigin']=source['channel']
            update['messageRef']=source['reference']
            update['original']['message']=source['message']
        response={'id':update['id'],'reference':'SB-'+update['id'].removeprefix('upd_')[:8].upper(),'projectId':projectid,'receivedAt':update['receivedAt'],'duplicate':False,'processingStatus':update.get('processingStatus')}
        workspace.store.batch([('updates',update),('remoteEntryReceipts',{'id':receiptid,'fingerprint':fingerprint,'response':response,'createdAt':timestamp()})])
    return response,201

def _local_json(url):
    # Never send local diagnostic requests through a configured external proxy.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url,timeout=.8) as response:return json.load(response)

def sharing_info(workspace):
    config=settings(workspace.store)
    receiver='http://127.0.0.1:'+str(config.get('receiverPort',8001))
    receiver_ready=False
    try:
        health=_local_json(receiver+'/entry/health')
        receiver_ready=health.get('app')=='SiteBrief entry receiver' and health.get('entries') is True
    except (OSError,ValueError):pass
    detected=''
    try:
        tunnels=_local_json('http://127.0.0.1:4040/api/tunnels').get('tunnels',[])
        for tunnel in tunnels:
            addr=str(tunnel.get('config',{}).get('addr',''))
            upstream=urllib.parse.urlsplit(addr if '://' in addr else 'http://'+addr)
            if upstream.hostname in {'127.0.0.1','localhost'} and upstream.port==config.get('receiverPort',8001) and str(tunnel.get('public_url','')).startswith('https://'):
                detected=tunnel['public_url'].rstrip('/');break
    except (OSError,ValueError):pass
    base=config.get('publicBaseUrl') or detected
    return {'projectId':config['projectId'],'enabled':config['enabled'],'accessCode':config['accessCode'],'publicBaseUrl':base,'detectedPublicUrl':detected,'shareUrl':base+'/submit#access='+urllib.parse.quote(config['accessCode']) if base else '', 'localFormUrl':receiver+'/submit','receiverReady':receiver_ready,'tunnelDetected':bool(detected),'receiverPort':config.get('receiverPort',8001)}

#!/usr/bin/env python3
"""SiteBrief local-workspace HTTP server. Run: python3 server.py"""
import argparse
import base64
import binascii
import copy
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import sys
import traceback
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from storage import Store
from pdf_export import create_report_pdf
from remote_entries import RemoteEntryError, sharing_info, save_settings
from bim_service import index_ifc, suggest_element, IfcIndexError, MAX_MODEL_BYTES

ROOT=Path(__file__).resolve().parent
PUBLIC=ROOT/'public'
ZONE=ZoneInfo('Asia/Singapore')
MAX_BODY=48*1024*1024
IMAGE_TYPES={'image/jpeg':('.jpg',12*1024*1024),'image/png':('.png',12*1024*1024),'image/webp':('.webp',12*1024*1024),'image/gif':('.gif',12*1024*1024)}
AUDIO_TYPES={'audio/mpeg':('.mp3',25*1024*1024),'audio/mp3':('.mp3',25*1024*1024),'audio/wav':('.wav',25*1024*1024),'audio/x-wav':('.wav',25*1024*1024),'audio/mp4':('.m4a',25*1024*1024),'audio/x-m4a':('.m4a',25*1024*1024),'audio/ogg':('.ogg',25*1024*1024),'audio/webm':('.webm',25*1024*1024)}
SECTIONS={'completed','ongoing','delays','issues','actions','uncertain','Completed work','Ongoing activities','Reported delays and blockers','Issues needing attention','Proposed follow-up actions','Missing or uncertain information'}

class ApiError(Exception):
    def __init__(self,message,status=400):self.message=message;self.status=status

def now():return dt.datetime.now(ZONE).isoformat(timespec='seconds')
def today():return dt.datetime.now(ZONE).date().isoformat()
def ident(prefix=''):return prefix+uuid.uuid4().hex[:16]
def stable(prefix,key):return prefix+hashlib.sha256(str(key).encode()).hexdigest()[:20]
def text(value,limit=10000):return str(value or '').strip()[:limit]
def valid_date(value,required=False):
    if not value:
        if required:raise ApiError('Please choose a date.')
        return ''
    try:dt.date.fromisoformat(str(value))
    except (TypeError,ValueError):raise ApiError('Use a valid date in YYYY-MM-DD format.')
    return str(value)
def valid_time(value):
    try:dt.time.fromisoformat(str(value))
    except (TypeError,ValueError):raise ApiError('Use a valid time in HH:MM format.')
    return str(value)
def review(value):
    if value not in {'pending','accepted','rejected'}:raise ApiError('Review decision must be pending, accepted or rejected.')
    return value

def ai_module():
    import ai_service
    return ai_service

class Workspace:
    def __init__(self,directory=None):
        self.store=Store(directory)
        if not self.store.all('projects'):
            self.store.put('projects',{'id':'workspace','name':'My construction project','code':'SB-001','location':'Local workspace','client':'Project team','phase':'Site operations'})
    def activity(self,title,description='',kind='update',sourceIds=None,projectId=None):
        sources=sourceIds or []
        if not projectId:
            projects={u.get('projectId') for sid in sources for u in [self.store.get('updates',sid)] if u and u.get('projectId')}
            if len(projects)==1:projectId=next(iter(projects))
        return {'id':ident('evt_'),'title':title,'description':description,'type':kind,'createdAt':now(),'sourceIds':sources,'projectId':projectId}
    def state(self):
        collections={name:self.store.all(name) for name in ('projects','updates','reports','issues','actions','activity','models','modelLinks')}
        collections['activity']=list(reversed(collections['activity']))[:80]
        collections['reports'].sort(key=lambda r:(r.get('date',''),r.get('createdAt','')),reverse=True)
        collections['updates'].sort(key=lambda u:(u.get('date',''),u.get('time','')),reverse=True)
        accepted_actions=[a for a in collections['actions'] if a.get('review')=='accepted']
        confirmed_issues=[i for i in collections['issues'] if i.get('review')=='accepted']
        collections['metrics']={
            'updatesToday':sum(u.get('date')==today() for u in collections['updates']),
            'reportsAwaitingReview':sum(r.get('status')=='Draft' for r in collections['reports']),
            'openIssues':sum(i.get('status')!='Resolved' for i in confirmed_issues),
            'urgentReview':sum(i.get('review')=='pending' and i.get('priority') in ('Urgent','Critical') for i in collections['issues']),
            'unassignedActions':sum(not a.get('owner') and a.get('status')!='Resolved' for a in accepted_actions),
            'overdueActions':sum(bool(a.get('deadline')) and a['deadline']<today() and a.get('status')!='Resolved' for a in accepted_actions)}
        try:config=ai_module().configuration()
        except (ImportError,AttributeError):config={'mode':'Demo','text':False,'images':False,'audio':False}
        collections.update({'mode':config.get('mode','Demo') if isinstance(config,dict) else 'Demo','ai':config,'today':today(),'workspace':'Local workspace'})
        return collections
    def require(self,collection,itemid):
        item=self.store.get(collection,itemid)
        if not item:raise ApiError('That record could not be found. Refresh and try again.',404)
        return item
    def project(self,itemid):return self.require('projects',itemid)
    def evidence(self,sourceids,projectid=None):
        if not isinstance(sourceids,list):raise ApiError('Evidence references must be a list.')
        result=[]
        for sid in sourceids:
            u=self.require('updates',str(sid))
            if projectid and u.get('projectId')!=projectid:raise ApiError('Evidence must belong to the same project.')
            if sid not in result:result.append(sid)
        return result
    def save_model(self,data,blob=None,demo=False):
        projectid=text(data.get('projectId'));self.project(projectid)
        raw=data.get('file') or {}
        if not isinstance(raw,dict):raise ApiError('Choose an IFC model file.')
        filename=Path(str(raw.get('name') or data.get('fileName') or 'model.ifc')).name[:180]
        if not filename.lower().endswith('.ifc'):raise ApiError('Upload an .ifc STEP model. IFCZIP, RVT, DWG and images are not supported.')
        if blob is None:
            encoded=raw.get('data','')
            if not isinstance(encoded,str) or not encoded:raise ApiError('Choose an IFC model file before uploading.')
            if encoded.startswith('data:'):encoded=encoded.split(',',1)[-1]
            try:blob=base64.b64decode(encoded,validate=True)
            except (ValueError,binascii.Error):raise ApiError('The IFC file could not be read. Select it again and retry.')
        if not isinstance(blob,bytes) or not blob:raise ApiError('The IFC file is empty.')
        if len(blob)>MAX_MODEL_BYTES:raise ApiError('The IFC is too large. Upload a model up to 25 MB or export a smaller building/discipline model.',413)
        digest=hashlib.sha256(blob).hexdigest()
        existing=next((m for m in self.store.all('models') if m.get('projectId')==projectid and m.get('sha256')==digest),None)
        if existing:return self.state()
        try:index=index_ifc(blob)
        except IfcIndexError as exc:raise ApiError(str(exc))
        mid=ident('model_');stored=mid+'.ifc'
        model={'id':mid,'projectId':projectid,'name':text(data.get('name') or Path(filename).stem,180),'fileName':filename,'storedName':stored,'url':'/api/models/'+mid+'/file','createdAt':now(),'sha256':digest,'size':len(blob),'processingStatus':'indexed','demo':bool(demo),**index}
        destination=self.store.directory/'uploads'/stored
        destination.write_bytes(blob)
        self.store.batch([('models',model),('activity',self.activity('IFC model uploaded',model['name']+' · '+str(index['elementCount'])+' indexed objects.','model',projectId=projectid))])
        return self.state()
    def load_demo_model(self,data):
        projectid=text(data.get('projectId'));self.project(projectid)
        source=PUBLIC/'demo'/'riverside.ifc'
        if not source.is_file():raise ApiError('The fictional demo model is unavailable. You can upload your own IFC model.',404)
        return self.save_model({'projectId':projectid,'name':'Riverside coordination model · fictional','file':{'name':'riverside.ifc'}},source.read_bytes(),demo=True)
    def model_suggestions(self,mid):
        model=self.require('models',mid);projectid=model['projectId']
        updates={u['id']:u for u in self.store.all('updates') if u.get('projectId')==projectid}
        linked=[link for link in self.store.all('modelLinks') if link.get('modelId')==mid]
        linked_issues={link.get('issueId') for link in linked if link.get('issueId')}
        linked_updates={link.get('updateId') for link in linked if link.get('updateId')}
        suggestions=[];unmatched_issues=[];unmatched_updates=[]
        for issue in self.store.all('issues'):
            if issue.get('projectId')!=projectid or issue.get('review')=='rejected' or issue.get('status')=='Resolved' or issue['id'] in linked_issues:continue
            sources=[updates[sid] for sid in issue.get('sourceIds',[]) if sid in updates]
            match=suggest_element(issue,model.get('elements',[]),sources)
            if match:suggestions.append({'id':stable('match_',mid+':issue:'+issue['id']),'modelId':mid,'projectId':projectid,'issueId':issue['id'],'recordType':'issue','title':issue.get('title','Reported issue'),'sourceIds':issue.get('sourceIds',[]),'review':'pending','origin':'Literal model/location match',**match})
            else:unmatched_issues.append(issue['id'])
        for update in updates.values():
            if update['id'] in linked_updates:continue
            match=suggest_element(update,model.get('elements',[]))
            if match:suggestions.append({'id':stable('match_',mid+':update:'+update['id']),'modelId':mid,'projectId':projectid,'updateId':update['id'],'recordType':'update','title':(update.get('location') or 'Site update')+' · '+update.get('contributor',''),'sourceIds':[update['id']],'review':'pending','origin':'Literal model/location match',**match})
            else:unmatched_updates.append(update['id'])
        return {'modelId':mid,'suggestions':suggestions,'unmatchedIssueIds':unmatched_issues,'unmatchedUpdateIds':unmatched_updates,'notice':'Literal IFC identifier, name or spatial-location suggestions only. Confirm each link. No defect detection or progress assessment has occurred.'}
    def save_model_link(self,lid,data):
        previous=self.require('modelLinks',lid) if lid else None
        modelid=text(data.get('modelId') or (previous or {}).get('modelId'));model=self.require('models',modelid)
        if previous and modelid!=previous['modelId']:raise ApiError('A saved model link cannot be moved to another model. Unlink it and create a new link.')
        projectid=model['projectId'];values={**(previous or {}),**data}
        target_ids={key:text(values.get(key)) for key in ('issueId','actionId','updateId')}
        if not any(target_ids.values()):raise ApiError('Choose an issue, action or original site update to link.')
        sourceids=[]
        for key,collection in (('issueId','issues'),('actionId','actions'),('updateId','updates')):
            if not target_ids[key]:continue
            record=self.require(collection,target_ids[key])
            if record.get('projectId')!=projectid:raise ApiError('The model and linked record must belong to the same project.')
            sourceids.extend([record['id']] if collection=='updates' else record.get('sourceIds',[]))
        reviewer=text(values.get('reviewer'),120)
        if not reviewer:raise ApiError('Enter the officer who is confirming this model link.')
        raw_express=values.get('expressId')
        if isinstance(raw_express,bool):raise ApiError('Choose a real IFC element or spatial location.')
        try:expressid=int(raw_express)
        except (ValueError,TypeError):raise ApiError('Choose an IFC element or indexed storey/space before confirming the link.')
        if str(raw_express).strip()!=str(expressid):raise ApiError('Choose a valid IFC Express ID.')
        element=next((e for e in model.get('elements',[]) if e['expressId']==expressid),None)
        if not element:raise ApiError('That object is not part of this IFC model. Select an indexed model object.')
        sourceids=self.evidence(list(dict.fromkeys(sourceids)),projectid)
        duplicate=next((link for link in self.store.all('modelLinks') if link.get('modelId')==modelid and link.get('expressId')==expressid and all(link.get(k,'')==v for k,v in target_ids.items()) and link.get('id')!=lid),None)
        if duplicate:
            if previous:raise ApiError('This record is already linked to that model object.')
            return self.state()
        link={**(previous or {}),'id':lid or ident('link_'),'modelId':modelid,'projectId':projectid,**target_ids,
              'expressId':expressid,'globalId':element['globalId'],'elementName':element['name'],'ifcType':element['ifcType'],'storey':element.get('storey',''),'location':element.get('location',''),
              'note':text(values.get('note'),8000),'reviewer':reviewer,'review':'accepted','origin':'Officer confirmed','matchLevel':'location' if element['ifcType'] in {'IFCSPACE','IFCBUILDINGSTOREY','IFCSPATIALZONE','IFCBUILDING','IFCSITE'} else 'element','sourceIds':sourceids,'createdAt':(previous or {}).get('createdAt',now()),'updatedAt':now()}
        self.store.batch([('modelLinks',link),('activity',self.activity('Model flag '+('updated' if previous else 'confirmed'),element['name']+' · '+reviewer,'model',sourceids,projectId=projectid))])
        return self.state()
    def delete_model_link(self,lid):
        link=self.require('modelLinks',lid)
        self.store.delete('modelLinks',lid)
        self.store.put('activity',self.activity('Model flag unlinked',link.get('elementName','Model object')+' · Original issue and evidence retained.','model',link.get('sourceIds',[]),projectId=link['projectId']))
        return self.state()
    def normalize_update(self,data):
        data=copy.deepcopy(data)
        data.setdefault('id',ident('upd_'));data.setdefault('projectId','workspace')
        data.setdefault('date',today());data.setdefault('time','09:00');data.setdefault('type','note')
        data.setdefault('location','');data.setdefault('contributor','');data.setdefault('text','');data.setdefault('caption','');data.setdefault('transcript','');data.setdefault('discipline','General')
        data.setdefault('createdAt',now());data.setdefault('updatedAt',data['createdAt']);data.setdefault('files',[])
        data.setdefault('original',{'text':data.get('text',''),'caption':data.get('caption',''),'transcript':data.get('transcript','')})
        for key in ('text','caption','transcript'):data.setdefault('original'+key.title(),data['original'].get(key,''))
        data.setdefault('processingStatus','ready' if data.get('text') or data.get('caption') or data.get('transcript') else 'manual_required')
        data.setdefault('processing',{'text':'not_processed','image':'not_processed','audio':'not_processed'})
        data['qualityAlerts']=self.update_alerts(data)
        return data
    def update_alerts(self,u):
        alerts=[]
        if not u.get('location'):alerts.append({'type':'missing_location','message':'Location is missing. Add the building, level or work area.'})
        if u.get('type')=='photo' and not u.get('caption'):alerts.append({'type':'manual_caption','message':'Add a caption explaining what this photo shows. Image analysis has not occurred.'})
        if u.get('type')=='audio' and not u.get('transcript'):alerts.append({'type':'manual_transcript','message':'Add a transcript or configure audio transcription. This recording has not been transcribed.'})
        if u.get('processingStatus')=='failed':alerts.append({'type':'processing_failed','message':u.get('processingError','Processing failed. Original evidence remains saved; retry or add a manual description.')})
        return alerts
    def load_demo(self):
        from demo_samples import get_demo_data
        demo=get_demo_data(today())
        projects=demo.get('projects') or [demo['project']]
        pending=[]
        for p in projects:
            if not self.store.get('projects',p['id']):pending.append(('projects',p))
        added=0
        for update in demo['updates']:
            if not self.store.get('updates',update['id']):pending.append(('updates',self.normalize_update(update)));added+=1
        if added:pending.append(('activity',self.activity('Demo project loaded',f'{added} fictional updates are ready for review.','demo',projectId=projects[0]['id'])))
        self.store.batch(pending)
        return self.state()
    def save_update(self,data,process_media=True,return_update=False):
        projectid=text(data.get('projectId'))
        self.project(projectid)
        kind=data.get('type','note')
        if kind not in ('note','chat','photo','audio'):raise ApiError('Choose a note, chat, photo or audio update.')
        contributor=text(data.get('contributor'),120)
        if not contributor:raise ApiError('Enter the contributor who supplied this update.')
        date=valid_date(data.get('date'),True);time=valid_time(data.get('time'))
        content={k:text(data.get(k),30000) for k in ('text','caption','transcript')}
        rawfiles=data.get('files',[])
        if not isinstance(rawfiles,list) or len(rawfiles)>6:raise ApiError('Upload up to six files in one update.')
        if kind in ('note','chat') and not content['text']:raise ApiError('Enter the site note or pasted chat message.')
        if kind in ('photo','audio') and not rawfiles:raise ApiError('Choose at least one photo or recording.')
        files=[];blobs=[]
        for raw in rawfiles:
            mime=str(raw.get('mime','')).lower().split(';')[0]
            allowed=IMAGE_TYPES if kind=='photo' else AUDIO_TYPES if kind=='audio' else {}
            if mime not in allowed:raise ApiError('Unsupported file type. Photos: JPG, PNG, WebP or GIF (12 MB each). Audio: MP3, WAV, M4A, OGG or WebM (25 MB each).')
            encoded=raw.get('data','')
            if not isinstance(encoded,str):raise ApiError('Invalid upload data. Please select the file again.')
            if encoded.startswith('data:'):encoded=encoded.split(',',1)[-1]
            try:blob=base64.b64decode(encoded,validate=True)
            except (ValueError,binascii.Error):raise ApiError('Could not read the uploaded file. Please select it again.')
            ext,limit=allowed[mime]
            if not blob:raise ApiError('Empty files cannot be uploaded.')
            if len(blob)>limit:raise ApiError(f'{raw.get("name","File")} is too large. Maximum size is {limit//(1024*1024)} MB.')
            # Verify file headers instead of trusting a browser supplied MIME string.
            signatures={'image/jpeg':blob.startswith(b'\xff\xd8\xff'),'image/png':blob.startswith(b'\x89PNG\r\n\x1a\n'),'image/gif':blob[:6] in (b'GIF87a',b'GIF89a'),'image/webp':blob[:4]==b'RIFF' and blob[8:12]==b'WEBP'}
            if mime in signatures and not signatures[mime]:raise ApiError('The photo contents do not match its file type. Upload a valid JPG, PNG, WebP or GIF.')
            if mime in ('audio/wav','audio/x-wav') and not (blob[:4]==b'RIFF' and blob[8:12]==b'WAVE'):raise ApiError('This WAV file is invalid. Please choose a playable recording.')
            audio_signatures={'audio/mpeg':blob.startswith(b'ID3') or (len(blob)>1 and blob[0]==255 and blob[1]&224==224),'audio/mp3':blob.startswith(b'ID3') or (len(blob)>1 and blob[0]==255 and blob[1]&224==224),'audio/mp4':b'ftyp' in blob[:32],'audio/x-m4a':b'ftyp' in blob[:32],'audio/ogg':blob.startswith(b'OggS'),'audio/webm':blob.startswith(b'\x1a\x45\xdf\xa3')}
            if mime in audio_signatures and not audio_signatures[mime]:raise ApiError('The recording contents do not match its file type. Choose a valid audio file.')
            fid=ident('file_');filename=fid+ext
            file={'id':fid,'name':Path(str(raw.get('name') or filename)).name[:180],'mime':mime,'size':len(blob),'url':'/files/'+fid,'storedName':filename}
            blobs.append((filename,blob));files.append(file)
        update=self.normalize_update({'id':ident('upd_'),'projectId':projectid,'location':text(data.get('location'),180),'date':date,'time':time,'contributor':contributor,'type':kind,**content,'discipline':text(data.get('discipline') or 'General',100),'files':files})
        # Write uploads first; a processing failure can never erase the original evidence.
        for filename,blob in blobs:(self.store.directory/'uploads'/filename).write_bytes(blob)
        if files and process_media:
            processing={'text':'not_processed','image':'not_processed','audio':'not_processed'}
            notices=[]
            for file in files:
                try:
                    result=ai_module().analyze_media(str(self.store.directory/'uploads'/file['storedName']),file['mime'])
                    if result.get('caption') and not update.get('caption'):update['caption']=result['caption'];update['captionOrigin']='AI interpretation - review required'
                    if result.get('transcript') and not update.get('transcript'):update['transcript']=result['transcript'];update['transcriptOrigin']='AI transcript - review required'
                    processing['image' if kind=='photo' else 'audio']=result.get('processing','not_processed')
                    if result.get('notice'):notices.append(result['notice'])
                except Exception as exc:
                    update['processingStatus']='failed';update['processingError']='Media processing failed. Your upload is safe. Add a caption/transcript or retry with configured services.'
                    notices.append(str(exc)[:160])
            update['processing']=processing;update['processingNotices']=notices
            if update['processingStatus']!='failed':update['processingStatus']='ready' if update.get('caption') or update.get('transcript') else 'manual_required'
            update['qualityAlerts']=self.update_alerts(update)
        if files and not process_media:
            update['processing']={'text':'not_processed','image':'manual' if kind=='photo' else 'not_processed','audio':'manual' if kind=='audio' else 'not_processed'}
            update['processingNotices']=['Remote upload saved. Supplied captions/transcripts are source evidence; media analysis has not occurred.']
        self.store.batch([('updates',update),('activity',self.activity('New '+kind+' update',contributor+' · '+(update['location'] or 'Location missing'),'update',[update['id']],projectId=update['projectId']))])
        return update if return_update else self.state()
    def edit_update(self,uid,data):
        update=self.require('updates',uid)
        for key in ('caption','transcript','location','discipline'):
            if key in data:update[key]=text(data[key],30000 if key in ('caption','transcript') else 180)
        update['updatedAt']=now();update['reviewerEdited']=True
        if 'caption' in data:update['captionOrigin']='Reviewer caption'
        if 'transcript' in data:update['transcriptOrigin']='Reviewer transcript'
        if update.get('caption') or update.get('transcript'):update['processingStatus']='manual_reviewed'
        update['qualityAlerts']=self.update_alerts(update)
        self.store.batch([('updates',update),('activity',self.activity('Evidence description updated','Original evidence is retained separately.','review',[uid],projectId=update['projectId']))])
        return self.state()
    def reconcile_extraction(self,generated,report_id,previous_findings):
        """Retain record identity when a provider emits different opaque keys.

        Only unambiguous evidence/category matches are reconciled. Multiple
        issues or actions with the same signature remain separate for review.
        """
        result=copy.deepcopy(generated)
        existing_issues=[i for i in self.store.all('issues') if i.get('reportId')==report_id]
        existing_actions=[a for a in self.store.all('actions') if a.get('reportId')==report_id]
        def sources(row):return tuple(sorted(set(str(s) for s in row.get('sourceIds',[]))))
        def category(row):return text(row.get('extractedCategory') or row.get('category')).casefold()
        def grouped(rows,signature):
            groups={}
            for row in rows:groups.setdefault(signature(row),[]).append(row)
            return groups
        def record_key(row):return str(row.get('key') or row.get('id') or '')
        issue_alias={};action_alias={}
        for issue in result.get('issues',[]):issue['extractedCategory']=issue.get('category','')
        issue_signature=lambda row:(sources(row),category(row))
        old_issues=grouped(existing_issues,issue_signature)
        new_issues=grouped(result.get('issues',[]),issue_signature)
        # Include the officer's corrected category as another exact alias while
        # retaining the originally extracted category for provider switches.
        for issue in existing_issues:
            signature=(sources(issue),text(issue.get('category')).casefold())
            rows=old_issues.setdefault(signature,[])
            if not any(i['id']==issue['id'] for i in rows):rows.append(issue)
        for issue in result.get('issues',[]):
            raw_key=record_key(issue);signature=issue_signature(issue)
            matches=old_issues.get(signature,[])
            if signature[0] and signature[1] and len(matches)==1 and len(new_issues.get(signature,[]))==1:
                matched=matches[0];issue['key']=record_key(matched);issue['_existingId']=matched['id']
            issue_alias[raw_key]=record_key(issue)
        all_issues={record_key(i):i for i in existing_issues}
        all_issues.update({record_key(i):i for i in result.get('issues',[])})
        issues_by_id={i['id']:i for i in existing_issues}
        def action_category(action):
            linked=all_issues.get(str(action.get('issueKey') or '')) or issues_by_id.get(action.get('issueId'))
            return category(linked) if linked else ''
        for action in result.get('actions',[]):
            if action.get('issueKey'):
                action['issueKey']=issue_alias.get(str(action['issueKey']),action['issueKey'])
                linked=all_issues.get(str(action['issueKey']))
                if linked and linked.get('_existingId'):action['issueId']=linked['_existingId']
        action_signature=lambda row:(sources(row),action_category(row))
        old_actions=grouped(existing_actions,action_signature)
        new_actions=grouped(result.get('actions',[]),action_signature)
        old_action_sources=grouped(existing_actions,sources)
        new_action_sources=grouped(result.get('actions',[]),sources)
        for action in result.get('actions',[]):
            raw_key=record_key(action);signature=action_signature(action)
            matches=old_actions.get(signature,[])
            unique_new=len(new_actions.get(signature,[]))==1
            # A missing linked issue can still match a sole action for the exact
            # same sources; known, different categories never use this fallback.
            if not signature[1]:
                matches=old_action_sources.get(signature[0],[])
                unique_new=len(new_action_sources.get(signature[0],[]))==1
            if signature[0] and len(matches)==1 and unique_new:
                matched=matches[0];action['key']=record_key(matched);action['_existingId']=matched['id']
            action_alias[raw_key]=record_key(action)
        for finding in result.get('findings',[]):
            if finding.get('issueKey'):finding['issueKey']=issue_alias.get(str(finding['issueKey']),finding['issueKey'])
            if finding.get('actionKey'):finding['actionKey']=action_alias.get(str(finding['actionKey']),finding['actionKey'])
        finding_signature=lambda row:(sources(row),row.get('section'),row.get('kind','fact'))
        old_findings=grouped(previous_findings,finding_signature)
        new_findings=grouped(result.get('findings',[]),finding_signature)
        old_keys={record_key(f) for f in previous_findings}
        for finding in result.get('findings',[]):
            if record_key(finding) in old_keys:continue
            signature=finding_signature(finding);matches=old_findings.get(signature,[])
            if signature[0] and len(matches)==1 and len(new_findings.get(signature,[]))==1:
                finding['key']=record_key(matches[0]);finding['_existingId']=matches[0]['id']
        return result
    def generate(self,data):
        projectid=text(data.get('projectId'));self.project(projectid);date=valid_date(data.get('date'),True)
        updates=[u for u in self.store.all('updates') if u['projectId']==projectid and u['date']==date]
        if not updates:raise ApiError('No updates for this project and date. Add an update or load the demo project first.')
        # Cache successful extraction against evidence content, not volatile
        # processing/review timestamps. Reprocessing unchanged inputs therefore
        # cannot create differently-keyed records from a stochastic AI response.
        source_fields=('id','projectId','location','date','time','contributor','type','text','caption','transcript','discipline','files','original','captionOrigin','transcriptOrigin')
        cache_input={'schema':'sitebrief-extraction-v1','projectId':projectid,'date':date,
            'sources':[{key:u.get(key) for key in source_fields} for u in sorted(updates,key=lambda row:row['id'])],
            'configuration':ai_module().configuration(),
            'models':{key:os.environ.get(key,'') for key in ('OPENAI_TEXT_MODEL','OPENAI_VISION_MODEL','OPENAI_TRANSCRIBE_MODEL','GEMINI_TEXT_MODEL','GEMINI_VISION_MODEL','GEMINI_TRANSCRIBE_MODEL','GEMINI_AUDIO_MODEL','GEMINI_MODEL')}}
        digest=hashlib.sha256(json.dumps(cache_input,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        cache_id='extraction_'+digest
        cached=self.store.get('extractions',cache_id)
        reused_extraction=bool(cached and isinstance(cached.get('result'),dict) and not cached['result'].get('processingError'))
        try:generated=copy.deepcopy(cached['result']) if reused_extraction else ai_module().generate_findings(updates)
        except Exception as exc:
            pending=[]
            for u in updates:
                u['processingStatus']='failed';u['processingError']='Report processing failed. Uploaded evidence and reviewed work are safe. Retry processing or correct the evidence description.';u['qualityAlerts']=self.update_alerts(u);pending.append(('updates',u))
            pending.append(('activity',self.activity('Report processing failed','Evidence retained. Retry after checking the AI configuration.','error',projectId=projectid)));self.store.batch(pending)
            print('Report processing error:',str(exc),file=sys.stderr)
            raise ApiError('Report processing failed. Your evidence and reviewed work are safe. Check the service configuration and retry.',502)
        rid=stable('rpt_',projectid+':'+date)
        existing=self.store.get('reports',rid)
        old={f.get('key',f['id']):f for f in (existing or {}).get('findings',[])}
        findings=[];pending=[];seen=set()
        if not reused_extraction and not generated.get('processingError'):
            pending.append(('extractions',{'id':cache_id,'digest':digest,'result':copy.deepcopy(generated),'createdAt':now()}))
        generated=self.reconcile_extraction(generated,rid,(existing or {}).get('findings',[]))
        for raw in generated.get('findings',[]):
            f=copy.deepcopy(raw);key=str(f.get('key') or f.get('id') or stable('',f.get('text','')+','.join(f.get('sourceIds',[]))))
            f['key']=key;f['id']=f.pop('_existingId',stable('f_',rid+key));f['sourceIds']=self.evidence(f.get('sourceIds',[]),projectid)
            if f.get('kind','fact')=='fact' and not f['sourceIds']:continue
            f.setdefault('kind','fact');f.setdefault('section','Missing or uncertain information');f.setdefault('review','pending');f.setdefault('edited',False)
            if key in old and (old[key].get('edited') or old[key].get('review')!='pending'):f=old[key]
            findings.append(f);seen.add(key)
        for key,f in old.items():
            if key not in seen and (f.get('edited') or f.get('review')!='pending'):findings.append(f)
        for collection in ('issues','actions'):
            for raw in generated.get(collection,[]):
                item=copy.deepcopy(raw);key=str(item.get('key') or item.get('id') or stable('',item.get('title','')+','.join(item.get('sourceIds',[]))))
                iid=item.pop('_existingId',stable('iss_' if collection=='issues' else 'act_',projectid+':'+date+':'+key))
                previous=self.store.get(collection,iid)
                if previous:continue
                item.update({'id':iid,'key':key,'projectId':projectid,'date':date,'review':'pending','status':'Open','createdAt':now(),'updatedAt':now(),'reportId':rid,'origin':'AI suggestion'})
                item['sourceIds']=self.evidence(item.get('sourceIds',[]),projectid)
                item.setdefault('location',next((u.get('location','') for u in updates if u['id'] in item['sourceIds']),''));item.setdefault('priority','Medium')
                if collection=='issues':item['extractedCategory']=item.get('category','')
                if collection=='actions':
                    item['owner']=text(item.get('owner'),120);item['deadline']=valid_date(item.get('deadline'))
                    item.setdefault('description','');item.setdefault('acceptance','Awaiting acceptance');item.setdefault('verification','Pending PM verification')
                    if item.get('issueKey'):item.setdefault('issueId',stable('iss_',projectid+':'+date+':'+str(item['issueKey'])))
                pending.append((collection,item))
        report={**(existing or {}),'id':rid,'projectId':projectid,'date':date,'status':'Draft','findings':findings,'reviewer':(existing or {}).get('reviewer',''),'approvedAt':None,'revision':(existing or {}).get('revision',0)+1,'createdAt':(existing or {}).get('createdAt',now()),'updatedAt':now(),'mode':generated.get('mode','Demo'),'alerts':generated.get('alerts',[]),'notice':generated.get('notice',''),'processingError':generated.get('processingError',''),'preservedEdits':bool(old),'reusedExtraction':reused_extraction}
        report.pop('approvalSnapshot',None)
        pending.append(('reports',report));pending.append(('activity',self.activity('Daily report generated',f'{date} · {len(findings)} findings ready for officer review.','report',[u['id'] for u in updates],projectId=projectid)))
        for u in updates:
            u['processingStatus']='failed' if generated.get('processingError') else 'processed';u['processedAt']=now()
            if generated.get('processingError'):u['processingError']='AI processing failed. Source-based Demo extraction was used; original evidence is safe.'
            else:u.pop('processingError',None)
            u['qualityAlerts']=self.update_alerts(u);pending.append(('updates',u))
        self.store.batch(pending)
        return self.state()
    def edit_report(self,rid,data):
        report=self.require('reports',rid);pending=[]
        action_updates={};issue_updates={}
        all_actions=self.store.all('actions');all_issues=self.store.all('issues')
        if 'findings' in data:
            if not isinstance(data['findings'],list):raise ApiError('Findings must be a list.')
            old={f['id']:f for f in report['findings']};merged=[]
            for raw in data['findings']:
                fid=raw.get('id')
                if fid not in old:raise ApiError('Finding could not be matched to the original report.')
                finding=copy.deepcopy(old[fid])
                if 'text' in raw:
                    newtext=text(raw['text'],12000)
                    if not newtext:raise ApiError('A finding cannot be blank. Reject it if it should be removed.')
                    if newtext!=finding['text']:finding['text']=newtext;finding['edited']=True;finding['editedAt']=now()
                if 'review' in raw:finding['review']=review(raw['review'])
                merged.append(finding)
                review_changed=finding.get('review')!=old[fid].get('review')
                text_changed=finding.get('text')!=old[fid].get('text')
                if finding.get('actionKey'):
                    for original_action in all_actions:
                        if original_action.get('reportId')==rid and original_action.get('key')==finding['actionKey']:
                            action=action_updates.get(original_action['id'],copy.deepcopy(original_action))
                            if review_changed:action['review']=finding['review']
                            if text_changed:action['description']=finding['text'];action['reviewerEdited']=True
                            if review_changed or text_changed:
                                action['updatedAt']=now();action_updates[action['id']]=action
                # A reported issue becomes a confirmed project record only after an
                # explicit changed review decision. Unchanged bulk saves do not
                # overwrite a separate issue review made by the officer.
                if finding.get('issueKey') and review_changed:
                    for original_issue in all_issues:
                        if original_issue.get('reportId')==rid and original_issue.get('key')==finding['issueKey']:
                            issue=issue_updates.get(original_issue['id'],copy.deepcopy(original_issue))
                            if finding['review']=='accepted' or (finding['review']=='rejected' and issue.get('review')=='pending'):
                                issue['review']=finding['review'];issue['updatedAt']=now();issue_updates[issue['id']]=issue
            # Allow a single finding patch without accidentally deleting other findings.
            changed={f['id']:f for f in merged}
            report['findings']=[changed.get(f['id'],f) for f in report['findings']]
        pending.extend(('actions',item) for item in action_updates.values())
        pending.extend(('issues',item) for item in issue_updates.values())
        if 'reviewer' in data:report['reviewer']=text(data['reviewer'],120)
        report.update({'status':'Draft','approvedAt':None,'revision':report.get('revision',0)+1,'updatedAt':now()});report.pop('approvalSnapshot',None)
        pending.append(('reports',report));pending.append(('activity',self.activity('Report review saved','Report is Draft and requires approval.','review',projectId=report['projectId'])));self.store.batch(pending)
        return self.state()
    def approve(self,rid,data):
        report=self.require('reports',rid);reviewer=text(data.get('reviewer') or report.get('reviewer'),120)
        if not reviewer:raise ApiError('Enter the reviewer name before approving the report.')
        if any(f.get('review')=='pending' for f in report['findings']):raise ApiError('Review every finding: accept or reject each item before approval.')
        if not any(f.get('review')=='accepted' for f in report['findings']):raise ApiError('Accept at least one reviewed finding before approving.')
        for f in report['findings']:
            if f.get('review')=='accepted' and f.get('kind')=='fact':
                if not f.get('sourceIds'):raise ApiError('Every factual finding must have supporting evidence.')
                self.evidence(f['sourceIds'],report['projectId'])
        report.update({'status':'Approved','reviewer':reviewer,'approvedAt':now(),'updatedAt':now()})
        actions=[copy.deepcopy(a) for a in self.store.all('actions') if a.get('projectId')==report['projectId'] and a.get('review')=='accepted' and a.get('status')!='Resolved']
        ids={sid for f in report['findings'] if f.get('review')=='accepted' for sid in f.get('sourceIds',[])}
        ids.update(sid for a in actions for sid in a.get('sourceIds',[]))
        clean=copy.deepcopy(report);clean.pop('approvalSnapshot',None)
        report['approvalSnapshot']={'report':clean,'project':self.project(report['projectId']),'actions':actions,'updates':[copy.deepcopy(u) for u in self.store.all('updates') if u['id'] in ids]}
        self.store.batch([('reports',report),('activity',self.activity('Daily report approved',reviewer+' approved the '+report['date']+' report.','approval',projectId=report['projectId']))])
        return self.state()
    def action(self,aid,data,return_action=False):
        old=self.require('actions',aid) if aid else None
        action=copy.deepcopy(old or {'id':ident('act_'),'createdAt':now(),'review':'accepted','status':'Open','sourceIds':[],'owner':'','deadline':'','priority':'Medium','acceptance':'Awaiting acceptance','verification':'Pending PM verification','origin':'External integration' if return_action else 'Officer created'})
        for key in ('title','description','projectId','location','owner','contractor','completionNote','acceptance','verification','issueId'):
            if key in data:action[key]=text(data[key],12000 if key in ('description','completionNote') else 200)
        if not action.get('title'):raise ApiError('Enter an action title.')
        self.project(action.get('projectId'))
        if 'sourceIds' in data:action['sourceIds']=self.evidence(data['sourceIds'],action['projectId'])
        else:action['sourceIds']=self.evidence(action.get('sourceIds',[]),action['projectId'])
        if action.get('issueId'):
            linked=self.require('issues',action['issueId'])
            if linked.get('projectId')!=action['projectId']:raise ApiError('Linked issue must belong to the same project.')
        if 'deadline' in data:action['deadline']=valid_date(data['deadline'])
        if 'priority' in data:
            if data['priority'] not in ('Low','Medium','High','Urgent','Critical'):raise ApiError('Choose a valid priority.')
            action['priority']=data['priority']
        if 'status' in data:
            if data['status'] not in ('Open','In Progress','Resolved'):raise ApiError('Choose Open, In Progress or Resolved.')
            action['status']=data['status']
        if 'review' in data:action['review']=review(data['review'])
        action['updatedAt']=now()
        if action['status']=='Resolved':action['resolvedAt']=action.get('resolvedAt') or now()
        else:action.pop('resolvedAt',None)
        pending=[('actions',action)]
        if old and action.get('review')!=old.get('review') and action.get('key'):
            for report in self.store.all('reports'):
                if action.get('reportId') and report['id']!=action['reportId']:continue
                changed=False
                for finding in report.get('findings',[]):
                    if finding.get('actionKey')==action['key'] and finding.get('review')!=action['review']:
                        finding['review']=action['review'];changed=True
                if changed:
                    report.update({'status':'Draft','approvedAt':None,'revision':report.get('revision',0)+1,'updatedAt':now()})
                    report.pop('approvalSnapshot',None);pending.append(('reports',report))
        pending.append(('activity',self.activity('Action '+('updated' if old else 'created'),action['title'],'action',action['sourceIds'],projectId=action['projectId'])))
        self.store.batch(pending)
        return action if return_action else self.state()
    def issue(self,iid,data):
        issue=self.require('issues',iid)
        for key in ('title','description','category','priority','status'):
            if key in data:issue[key]=text(data[key],10000 if key=='description' else 200)
        if issue.get('priority') not in ('Low','Medium','High','Urgent','Critical'):raise ApiError('Choose a valid priority.')
        if issue.get('status') not in ('Open','In Progress','Resolved'):raise ApiError('Choose Open, In Progress or Resolved.')
        if 'review' in data:issue['review']=review(data['review'])
        issue['updatedAt']=now()
        self.store.batch([('issues',issue),('activity',self.activity('Issue review saved',issue.get('title',''),'review',issue.get('sourceIds',[]),projectId=issue['projectId']))])
        return self.state()

class Handler(SimpleHTTPRequestHandler):
    workspace=None
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(PUBLIC),**kwargs)
    def log_message(self,format,*args):
        if os.environ.get('SITEBRIEF_QUIET')!='1':super().log_message(format,*args)
    def send_json(self,value,status=200):
        encoded=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(encoded)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(encoded)
    def read_json(self):
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:raise ApiError('Invalid request length.')
        if length>MAX_BODY:raise ApiError('Upload is too large. Send fewer files at once.',413)
        if length<=0:return {}
        if 'application/json' not in self.headers.get('Content-Type',''):raise ApiError('Use JSON requests for this endpoint.',415)
        try:value=json.loads(self.rfile.read(length))
        except (json.JSONDecodeError,UnicodeDecodeError):raise ApiError('The request could not be read. Please try again.')
        if not isinstance(value,dict):raise ApiError('The request must contain a JSON object.')
        return value
    def route(self,method):
        parsed=urlparse(self.path);path=unquote(parsed.path).rstrip('/') or '/'
        w=self.workspace
        if method=='GET' and path=='/api/health':return self.send_json({'app':'SiteBrief','status':'ok'})
        if method=='GET' and path=='/api/state':return self.send_json(w.state())
        if method=='GET' and path=='/api/entry-sharing':return self.send_json(sharing_info(w))
        if method=='GET' and re.fullmatch(r'/api/models/[^/]+/suggestions',path):return self.send_json(w.model_suggestions(path.split('/')[3]))
        if method=='GET' and re.fullmatch(r'/api/models/[^/]+/file',path):
            model=w.require('models',path.split('/')[3]);disk=w.store.directory/'uploads'/Path(model['storedName']).name
            if not disk.is_file():raise ApiError('The original IFC model file is unavailable on disk.',404)
            body=disk.read_bytes();self.send_response(200);self.send_header('Content-Type','application/x-step');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body);return
        if method=='DELETE' and re.fullmatch(r'/api/model-links/[^/]+',path):
            with w.store.lock:return self.send_json(w.delete_model_link(path.split('/')[3]))
        if method=='GET' and re.fullmatch(r'/api/reports/[^/]+/pdf',path):
            report=w.require('reports',path.split('/')[3])
            if report.get('status')!='Approved' or not report.get('approvalSnapshot'):raise ApiError('Approve the report before downloading its PDF.',409)
            body=create_report_pdf(report['approvalSnapshot']);self.send_response(200);self.send_header('Content-Type','application/pdf');self.send_header('Content-Disposition','attachment; filename="SiteBrief-'+report['date']+'.pdf"');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body);return
        if method=='GET' and path.startswith('/files/'):
            fid=path[7:]
            file=next((f for u in w.store.all('updates') for f in u.get('files',[]) if f.get('id')==fid),None)
            if not file or not file.get('storedName'):raise ApiError('Uploaded file not found.',404)
            disk=w.store.directory/'uploads'/Path(file['storedName']).name
            if not disk.is_file():raise ApiError('The original upload is unavailable on disk.',404)
            body=disk.read_bytes();self.send_response(200);self.send_header('Content-Type',file['mime']);self.send_header('Content-Length',str(len(body)));self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body);return
        if method in ('POST','PATCH'):
            data=self.read_json()
            with w.store.lock:
                if method=='POST' and path=='/api/entry-sharing':
                    save_settings(w,data);return self.send_json(sharing_info(w))
                if method=='POST' and path=='/api/models':return self.send_json(w.save_model(data),201)
                if method=='POST' and path=='/api/models/demo':return self.send_json(w.load_demo_model(data))
                if method=='POST' and path=='/api/model-links':return self.send_json(w.save_model_link(None,data),201)
                if method=='PATCH' and re.fullmatch(r'/api/model-links/[^/]+',path):return self.send_json(w.save_model_link(path.split('/')[3],data))
                if method=='POST' and path=='/api/demo':return self.send_json(w.load_demo())
                if method=='POST' and path=='/api/updates':return self.send_json(w.save_update(data),201)
                if method=='PATCH' and re.fullmatch(r'/api/updates/[^/]+',path):return self.send_json(w.edit_update(path.split('/')[3],data))
                if method=='POST' and path=='/api/reports/generate':return self.send_json(w.generate(data))
                if method=='PATCH' and re.fullmatch(r'/api/reports/[^/]+',path):return self.send_json(w.edit_report(path.split('/')[3],data))
                if method=='POST' and re.fullmatch(r'/api/reports/[^/]+/approve',path):return self.send_json(w.approve(path.split('/')[3],data))
                if method=='POST' and path=='/api/actions':return self.send_json(w.action(None,data),201)
                if method=='PATCH' and re.fullmatch(r'/api/actions/[^/]+',path):return self.send_json(w.action(path.split('/')[3],data))
                if method=='PATCH' and re.fullmatch(r'/api/issues/[^/]+',path):return self.send_json(w.issue(path.split('/')[3],data))
                if method=='POST' and re.fullmatch(r'/api/actions/[^/]+/remind',path):
                    action=w.require('actions',path.split('/')[3]);action['lastReminderAt']=now();w.store.batch([('actions',action),('activity',w.activity('Follow-up reminder recorded',action['title']+' · Local workspace reminder. No external message sent.','reminder',action.get('sourceIds',[]),projectId=action['projectId']))]);return self.send_json(w.state())
        if path.startswith('/api/'):raise ApiError('API endpoint not found.',404)
        if method=='GET':
            # Only serve public assets; data, environment files and Python source remain private.
            if '..' in Path(path).parts:raise ApiError('Invalid path.',400)
            if path=='/' or ('.' not in Path(path).name):self.path='/index.html'
            return super().do_GET()
        raise ApiError('Endpoint not found.',404)
    def handle_method(self,method):
        try:self.route(method)
        except (ApiError,RemoteEntryError) as exc:self.send_json({'error':exc.message},exc.status)
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception:
            traceback.print_exc();self.send_json({'error':'Something went wrong. Your saved data is safe. Refresh and try again.'},500)
    def do_GET(self):self.handle_method('GET')
    def do_POST(self):self.handle_method('POST')
    def do_PATCH(self):self.handle_method('PATCH')
    def do_DELETE(self):self.handle_method('DELETE')

def main():
    parser=argparse.ArgumentParser(description='Run SiteBrief local workspace')
    parser.add_argument('--host',default=os.environ.get('SITEBRIEF_HOST','127.0.0.1'))
    parser.add_argument('--port',type=int,default=int(os.environ.get('PORT','8000')))
    args=parser.parse_args();Handler.workspace=Workspace()
    httpd=ThreadingHTTPServer((args.host,args.port),Handler)
    print(f'SiteBrief running at http://{args.host}:{args.port}',flush=True)
    print(f'Data is saved in {Handler.workspace.store.directory}',flush=True)
    try:httpd.serve_forever()
    except KeyboardInterrupt:print('\nSiteBrief stopped.')
    finally:httpd.server_close()
if __name__=='__main__':main()

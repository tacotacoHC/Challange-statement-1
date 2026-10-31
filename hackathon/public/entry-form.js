'use strict';
const el=id=>document.getElementById(id);
const escapeText=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const entry={code:'',context:null,kind:'note',files:[],requestKey:'',saving:false,reading:false};
const fileTypes={photo:['image/jpeg','image/png','image/webp','image/gif'],audio:['audio/mpeg','audio/mp3','audio/wav','audio/x-wav','audio/mp4','audio/x-m4a','audio/ogg','audio/webm']};
function showError(id,message){el(id).textContent=message;el(id).hidden=!message;}
function newReference(){return globalThis.crypto?.randomUUID?.()||'entry-'+Date.now()+'-'+Math.random().toString(36).slice(2);}
async function request(path,options={}){
  let response;
  try{response=await fetch(path,{...options,headers:{'Authorization':'Bearer '+entry.code,'ngrok-skip-browser-warning':'SiteBrief',...(options.headers||{})}});}
  catch{throw Error('The connection to the site team was interrupted. Your form is still open. Retry when the connection returns or contact your site officer.');}
  let body;try{body=await response.json();}catch{throw Error('The entry receiver did not respond correctly. Ask your site officer to check that the receiver and ngrok are still running.');}
  if(!response.ok){const error=Error(body.error||'The entry could not be saved.');error.status=response.status;throw error;}
  return body;
}
async function connect(event){
  event?.preventDefault();showError('access-error','');entry.code=el('access-code').value.trim();
  const button=el('connect-button');button.disabled=true;button.textContent='Connecting…';
  try{
    const context=await request('/entry/context');entry.context=context;
    try{sessionStorage.setItem('sitebrief-entry-code',entry.code);}catch{}
    el('project-name').textContent=context.project.name;
    if(!el('date').value)el('date').value=context.today;
    if(!el('time').value)el('time').value=context.time;
    if(!el('contributor').value){try{el('contributor').value=sessionStorage.getItem('sitebrief-contributor')||'';}catch{}}
    el('access-card').hidden=true;el('entry-card').hidden=false;el('success-card').hidden=true;
    setKind(entry.kind);el('contributor').focus();
  }catch(error){showError('access-error',error.message);}
  finally{button.disabled=false;button.innerHTML='Connect to project <span aria-hidden="true">→</span>';}
}
function setKind(kind){
  const changed=entry.kind!==kind;entry.kind=kind;
  if(changed){entry.files=[];el('files').value='';renderPreviews();}
  document.querySelectorAll('[data-kind]').forEach(button=>{button.classList.toggle('selected',button.dataset.kind===kind);button.setAttribute('aria-pressed',String(button.dataset.kind===kind));});
  const media=kind==='photo'||kind==='audio';
  el('text-field').hidden=media;el('text').required=!media;
  el('text-label').textContent=kind==='chat'?'Pasted chat message':'Site note';
  el('files-field').hidden=!media;el('caption-field').hidden=kind!=='photo';el('transcript-field').hidden=kind!=='audio';
  if(media){el('files').accept=fileTypes[kind].join(',');el('upload-title').textContent=kind==='photo'?'Choose site photos':'Choose voice recordings';el('file-limits').textContent=(kind==='photo'?'JPG, PNG, WebP, GIF · 12 MB each':'MP3, WAV, M4A, OGG, WebM · 25 MB each')+' · up to 6 files, 34 MB total';}
  el('entry-notice').textContent=media?'The original files will be saved for officer review. Please supply a caption or transcript when available; automatic media analysis does not run during remote submission.':'Your update is saved as reported evidence for officer review. Include an owner or deadline only when one was actually stated.';
}
function renderPreviews(){
  el('previews').innerHTML=entry.files.map((file,index)=>`<div class="preview">${file.mime.startsWith('image/')?`<img src="${file.url}" alt="Selected site photo">`:`<audio controls src="${file.url}" aria-label="Selected recording"></audio>`}<span>${escapeText(file.name)} · ${(file.size/1024/1024).toFixed(1)} MB</span><button type="button" class="text-button" data-remove-file="${index}" aria-label="Remove ${escapeText(file.name)}">Remove</button></div>`).join('');
}
function inferMime(file){return file.type||({m4a:'audio/mp4',mp3:'audio/mpeg',wav:'audio/wav',ogg:'audio/ogg'}[file.name.split('.').at(-1).toLowerCase()]||'');}
async function readFiles(files){
  showError('entry-error','');entry.reading=true;el('submit-button').disabled=true;
  try{
    const selected=Array.from(files);
    if(selected.length>6)throw Error('Choose up to six files for one update.');
    let total=0;const kind=entry.kind;
    for(const file of selected){const mime=inferMime(file);if(!fileTypes[kind]?.includes(mime))throw Error(file.name+': this file type is not supported.');if(!file.size)throw Error(file.name+': the file is empty.');if(file.size>(kind==='photo'?12:25)*1024*1024)throw Error(file.name+': maximum size is '+(kind==='photo'?12:25)+' MB.');total+=file.size;}
    if(total>34*1024*1024)throw Error('The files total more than 34 MB. Send fewer files at a time.');
    const result=await Promise.all(selected.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,mime:inferMime(file),size:file.size,url:reader.result,data:String(reader.result).split(',')[1]});reader.onerror=()=>reject(Error('Could not read '+file.name));reader.readAsDataURL(file);})));
    if(entry.kind!==kind)return;entry.files=result;renderPreviews();
  }catch(error){entry.files=[];el('files').value='';renderPreviews();showError('entry-error',error.message);}
  finally{entry.reading=false;el('submit-button').disabled=false;}
}
async function submit(event){
  event.preventDefault();if(entry.saving||entry.reading||!entry.context)return;
  showError('entry-error','');
  const values=Object.fromEntries(new FormData(el('entry-form')));
  const payload={projectId:entry.context.project.id,type:entry.kind,contributor:values.contributor.trim(),location:values.location.trim(),discipline:values.discipline,date:values.date,time:values.time,text:['note','chat'].includes(entry.kind)?values.text:'',caption:entry.kind==='photo'?values.caption:'',transcript:entry.kind==='audio'?values.transcript:'',files:entry.files.map(({name,mime,data})=>({name,mime,data}))};
  if(['photo','audio'].includes(entry.kind)&&!payload.files.length){showError('entry-error','Choose at least one photo or recording.');return;}
  entry.requestKey=entry.requestKey||newReference();entry.saving=true;const button=el('submit-button');button.disabled=true;button.textContent='Sending your update…';
  try{
    const result=await request('/webhooks/updates',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':entry.requestKey},body:JSON.stringify(payload)});
    el('receipt-project').textContent=entry.context.project.name;
    el('receipt-reference').textContent=result.reference;
    el('entry-card').hidden=true;el('success-card').hidden=false;el('success-card').scrollIntoView({behavior:'smooth',block:'start'});
    try{sessionStorage.setItem('sitebrief-contributor',values.contributor);}catch{}
  }catch(error){
    showError('entry-error',error.message);
    if(error.status===401){el('entry-card').hidden=true;el('access-card').hidden=false;showError('access-error','Your entry code needs to be re-entered. The form values are retained.');}
    if(error.status===409){let reset=el('new-reference');if(!reset){reset=document.createElement('button');reset.type='button';reset.id='new-reference';reset.className='text-button';reset.textContent='Use a new reference for a separate entry';reset.addEventListener('click',()=>{entry.requestKey=newReference();reset.remove();showError('entry-error','A new reference is ready. Sending now will create a separate entry.');});el('entry-error').after(reset);}}
  }finally{entry.saving=false;button.disabled=false;button.innerHTML='Send site update <span aria-hidden="true">→</span>';}
}
el('access-form').addEventListener('submit',connect);
el('entry-form').addEventListener('submit',submit);
document.querySelectorAll('[data-kind]').forEach(button=>button.addEventListener('click',()=>setKind(button.dataset.kind)));
el('files').addEventListener('change',event=>readFiles(event.target.files));
el('previews').addEventListener('click',event=>{const button=event.target.closest('[data-remove-file]');if(button){entry.files.splice(Number(button.dataset.removeFile),1);renderPreviews();}});
el('disconnect').addEventListener('click',()=>{try{sessionStorage.removeItem('sitebrief-entry-code');}catch{}entry.code='';entry.context=null;el('entry-card').hidden=true;el('access-card').hidden=false;el('access-code').value='';el('access-code').focus();});
el('another').addEventListener('click',()=>{const name=el('contributor').value;el('entry-form').reset();el('contributor').value=name;el('date').value=entry.context.today;el('time').value=entry.context.time;entry.files=[];entry.requestKey='';renderPreviews();setKind('note');el('new-reference')?.remove();showError('entry-error','');el('success-card').hidden=true;el('entry-card').hidden=false;el('text').focus();});
try{const fromLink=new URLSearchParams(location.hash.slice(1)).get('access');entry.code=fromLink||sessionStorage.getItem('sitebrief-entry-code')||'';if(fromLink)history.replaceState(null,'',location.pathname);if(entry.code){el('access-code').value=entry.code;connect();}}catch{}

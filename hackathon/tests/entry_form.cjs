/* Public entry-form behavior against a real isolated receiver, without a browser. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm');
const {spawn,spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'sitebrief-form-'));
const env={...process.env,SITEBRIEF_DATA_DIR:dir,AI_PROVIDER:'demo',GEMINI_API_KEY:'',GOOGLE_API_KEY:'',OPENAI_API_KEY:''};
const code='test-entry-form-code';
const seed=spawnSync('python3',['-c',"from server import Workspace;from remote_entries import settings;w=Workspace();c=settings(w.store);c['accessCode']='test-entry-form-code';w.store.put('remoteEntrySettings',c)"],{cwd:root,env});
assert.equal(seed.status,0);
const nodes=new Map(),kindButtons=['note','chat','photo','audio'].map(kind=>({dataset:{kind},classList:{toggle(){}},setAttribute(){},addEventListener(){}}));
function element(id=''){const value={id,value:'',hidden:false,disabled:false,innerHTML:'',textContent:'',dataset:{},listeners:{},classList:{toggle(){}},addEventListener(type,handler){this.listeners[type]=handler;},setAttribute(){},focus(){},scrollIntoView(){},remove(){if(this.id)nodes.delete(this.id);},after(item){if(item.id)nodes.set(item.id,item);},reset(){for(const key of ['text','caption','transcript','location','contributor','date','time'])nodes.get(key).value='';}};return value;}
const html=fs.readFileSync(path.join(root,'public/entry-form.html'),'utf8');for(const match of html.matchAll(/id="([^"]+)"/g))nodes.set(match[1],element(match[1]));
nodes.get('discipline').value='General';nodes.get('entry-card').hidden=true;nodes.get('success-card').hidden=true;
const doc={getElementById:id=>nodes.get(id)||null,querySelectorAll:q=>q==='[data-kind]'?kindButtons:[],createElement:()=>element()};
const store=new Map();let server;
async function main(){
 server=spawn('python3',['webhook_server.py','--port','0'],{cwd:root,env,stdio:['ignore','pipe','pipe']});let output='';const port=await new Promise((resolve,reject)=>{server.stdout.on('data',b=>{output+=b;const found=output.match(/listening on 127\.0\.0\.1:(\d+)/);if(found)resolve(found[1]);});server.on('exit',()=>reject(Error('Receiver did not start')));});
 const base='http://127.0.0.1:'+port;
 const fields=['contributor','location','date','time','discipline','text','caption','transcript'];
 function Reader(){this.readAsDataURL=file=>{this.result='data:'+file.type+';base64,'+file.data;this.onload();};}
 const context=vm.createContext({document:doc,sessionStorage:{getItem:k=>store.get(k)||'',setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},location:{hash:'',pathname:'/submit'},history:{replaceState(){}},URLSearchParams,FileReader:Reader,FormData:function(){return new Map(fields.map(id=>[id,nodes.get(id).value]));},fetch:(url,opt)=>fetch(base+url,opt),crypto:require('node:crypto').webcrypto,console,Date});
 new vm.Script(fs.readFileSync(path.join(root,'public/entry-form.js'),'utf8')).runInContext(context);
 const run=src=>vm.runInContext(src,context),submitEvent={preventDefault(){}};context.event=submitEvent;
 nodes.get('access-code').value=code;await run('connect(event)');assert.equal(nodes.get('entry-card').hidden,false);assert.equal(nodes.get('project-name').textContent,'My construction project');
 nodes.get('contributor').value='Remote form QA';nodes.get('location').value='Basement';nodes.get('text').value='Pump P-02 is not starting. No owner or deadline is stated.';
 const connected=context.fetch;let interrupted=false;
 context.fetch=async(url,opt)=>{const response=await connected(url,opt);if(url==='/webhooks/updates'&&!interrupted){interrupted=true;throw Error('Connection lost after receipt');}return response;};
 await run('submit(event)');assert.equal(nodes.get('success-card').hidden,true);assert.match(nodes.get('entry-error').textContent,/interrupted/);assert.match(nodes.get('text').value,/Pump P-02/);const reference=run('entry.requestKey');
 context.fetch=connected;await run('submit(event)');assert.equal(nodes.get('success-card').hidden,false);assert.equal(run('entry.requestKey'),reference);assert.match(nodes.get('receipt-reference').textContent,/^SB-/);
 nodes.get('another').listeners.click();assert.equal(nodes.get('entry-card').hidden,false);assert.equal(run('entry.requestKey'),'');assert.equal(nodes.get('contributor').value,'Remote form QA');
 run("setKind('photo')");assert.equal(nodes.get('text').required,false);assert.equal(nodes.get('caption-field').hidden,false);
 context.invalid=[{name:'unsupported.exe',type:'application/octet-stream',size:3}];await run('readFiles(invalid)');assert.match(nodes.get('entry-error').textContent,/not supported/);assert.equal(run('entry.files.length'),0);
 const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8i8AAAAASUVORK5CYII=';
 context.photo=[{name:'<site>.png',type:'image/png',size:Buffer.from(png,'base64').length,data:png}];await run('readFiles(photo)');assert.equal(run('entry.files.length'),1);assert.match(nodes.get('previews').innerHTML,/&lt;site&gt;/);
 nodes.get('caption').value='Manual photo caption from the field.';await run('submit(event)');assert.equal(nodes.get('success-card').hidden,false);
 const verify=spawnSync('python3',['-c',"from storage import Store;import json;s=Store();u=s.all('updates');print(json.dumps({'count':len(u),'origins':[x.get('submissionOrigin') for x in u],'imageProcessing':[x.get('processing',{}).get('image') for x in u if x['type']=='photo']}))"],{cwd:root,env,encoding:'utf8'});assert.equal(verify.status,0);const saved=JSON.parse(verify.stdout);assert.equal(saved.count,2);assert(saved.origins.every(o=>o==='Remote entry'));assert.equal(saved.imageProcessing[0],'manual');
 nodes.get('disconnect').listeners.click();assert.equal(nodes.get('access-card').hidden,false);assert.equal(store.has('sitebrief-entry-code'),false);
 console.log('PASS: public entry form connects, retains interrupted submissions, deduplicates retries, previews files and saves original evidence.');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{server?.kill('SIGTERM');fs.rmSync(dir,{recursive:true,force:true});});

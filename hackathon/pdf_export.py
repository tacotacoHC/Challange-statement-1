"""Dependency-free, paginated PDF export of an immutable approved report snapshot.
PDF text is escaped and wrapped; content comes exclusively from the approval snapshot.
"""
import textwrap
import hashlib
import unicodedata

def ascii_text(value):
    value = str(value or '').replace('\u2013','-').replace('\u2014','-').replace('\u2019',"'").replace('\u2022','-').replace('\u00b7',' / ')
    return unicodedata.normalize('NFKD',value).encode('ascii','replace').decode('ascii')

def escape(value):
    return ascii_text(value).replace('\\','\\\\').replace('(','\\(').replace(')','\\)')

def evidence_ref(source_id):
    return 'E-'+hashlib.sha256(str(source_id).encode()).hexdigest()[:8].upper()

def create_report_pdf(snapshot):
    report = snapshot.get('report', snapshot)
    project = snapshot.get('project', {})
    updates = {u['id']:u for u in snapshot.get('updates',[])}
    lines = []
    group_number=0
    def add(text='', bold=False, size=10):
        nonlocal group_number
        group_number+=1
        for segment in str(text).splitlines() or ['']:
            for wrapped in textwrap.wrap(ascii_text(segment),width=94 if size<=10 else 77,break_long_words=True,replace_whitespace=True) or ['']:
                lines.append((wrapped,bold,size,group_number))
    add('SITEBRIEF | DAILY SITE REPORT',True,17)
    add(project.get('name','Construction project'),True,13)
    add('Project: '+project.get('code',project.get('id',''))+'   |   Location: '+project.get('location','Not specified'))
    add('Report date: '+report.get('date','')+'   |   State: Approved   |   Revision: '+str(report.get('revision',1)))
    add('Reviewed by: '+str(report.get('reviewer',''))+'   |   Approved: '+str(report.get('approvedAt','')))
    add('All findings below were reviewed. Suggested actions remain labelled as suggestions.')
    add()
    labels = [('completed','Completed work'),('ongoing','Ongoing activities'),('delays','Reported delays and blockers'),('issues','Issues needing attention'),('actions','Follow-up actions'),('uncertain','Missing or uncertain information')]
    aliases={'completed':{'completed','Completed work'},'ongoing':{'ongoing','Ongoing activities'},'delays':{'delays','Delays & blockers','Reported delays and blockers'},'issues':{'issues','Issues needing attention'},'actions':{'actions','Proposed follow-up actions','Follow-up actions'},'uncertain':{'uncertain','Missing & uncertain','Missing or uncertain information'}}
    known = set()
    for section,label in labels:
        findings=[f for f in report.get('findings',[]) if f.get('review')=='accepted' and f.get('section') in aliases[section]]
        known.update(aliases[section])
        add(label.upper(),True,12)
        if not findings: add('No reviewed findings recorded for this section.')
        for index,f in enumerate(findings,1):
            kind = 'AI suggestion, reviewed' if f.get('kind')=='suggestion' else 'Reported fact, reviewed'
            add(str(index)+'. '+f.get('text',''))
            add('   '+kind+' | Evidence: '+', '.join(evidence_ref(sid) for sid in f.get('sourceIds',[])))
        add()
    extras=[f for f in report.get('findings',[]) if f.get('review')=='accepted' and f.get('section') not in known]
    if extras:
        add('OTHER REVIEWED FINDINGS',True,12)
        for f in extras:
            add(f.get('text',''))
            add('Evidence: '+', '.join(evidence_ref(sid) for sid in f.get('sourceIds',[])))
        add()
    add('OUTSTANDING ACTIONS AT APPROVAL',True,12)
    actions=snapshot.get('actions',[])
    if not actions: add('No confirmed outstanding actions.')
    for action in actions:
        add(action.get('title',''),True)
        add(action.get('description',''))
        add('Owner: '+(action.get('owner') or 'Unassigned')+' | Deadline: '+(action.get('deadline') or 'Unset')+' | Priority: '+action.get('priority','Medium')+' | Status: '+action.get('status','Open'))
        add('Location: '+(action.get('location') or 'Not specified')+' | Evidence: '+', '.join(evidence_ref(sid) for sid in action.get('sourceIds',[])))
        add()
    add('EVIDENCE REGISTER',True,12)
    for update in updates.values():
        add(evidence_ref(update['id'])+' | '+update.get('type','note').upper()+' | '+update.get('contributor','')+' | '+update.get('date','')+' '+update.get('time','')+' | '+(update.get('location') or 'Location not provided'),True)
        original=update.get('original',{})
        text=original.get('text') or original.get('caption') or original.get('transcript') or update.get('text') or update.get('caption') or update.get('transcript') or 'Media upload; see the original evidence in SiteBrief.'
        add(text)
        for file in update.get('files',[]): add('Attachment: '+file.get('name','Evidence file'))
        add()
    # Keep wrapped paragraphs together, and a heading with the following paragraph.
    # This avoids orphaned action titles and split short source excerpts.
    blocks=[]
    for line in lines:
        if not blocks or blocks[-1][0][3]!=line[3]:blocks.append([])
        blocks[-1].append(line)
    pages=[];current=[];height=0
    for index,block in enumerate(blocks):
        needed=sum(max(14,line[2]+5) for line in block)
        if block[0][1]:
            following=index+1
            while following<len(blocks):
                needed+=sum(max(14,line[2]+5) for line in blocks[following])
                if not blocks[following][0][1]:break
                following+=1
        if current and height+needed>690 and needed<=690:
            pages.append(current);current=[];height=0
        for line in block:
            lineheight=max(14,line[2]+5)
            if current and height+lineheight>690:
                pages.append(current);current=[];height=0
            if not current and not line[0]:continue
            current.append(line);height+=lineheight
    if current:pages.append(current)
    objects=[]
    def obj(content):objects.append(content.encode('latin-1') if isinstance(content,str) else content);return len(objects)
    catalog=obj('')
    pagesobj=obj('')
    font=obj('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    boldfont=obj('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>')
    pageids=[]
    for pno,page in enumerate(pages,1):
        commands=['BT','1 0 0 1 48 792 Tm']
        for value,bold,size,_ in page:
            commands.extend([('/F2' if bold else '/F1')+' '+str(size)+' Tf','('+escape(value)+') Tj','0 -'+str(max(14,size+5))+' Td'])
        commands+=['ET','BT /F1 8 Tf 1 0 0 1 48 32 Tm (SiteBrief - Approved report | Page '+str(pno)+' of '+str(len(pages))+') Tj ET']
        stream='\n'.join(commands).encode('latin-1')
        contentid=obj(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
        pageid=obj('<< /Type /Page /Parent '+str(pagesobj)+' 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 '+str(font)+' 0 R /F2 '+str(boldfont)+' 0 R >> >> /Contents '+str(contentid)+' 0 R >>')
        pageids.append(pageid)
    objects[catalog-1]=('<< /Type /Catalog /Pages '+str(pagesobj)+' 0 R >>').encode()
    objects[pagesobj-1]=('<< /Type /Pages /Count '+str(len(pageids))+' /Kids ['+' '.join(str(i)+' 0 R' for i in pageids)+'] >>').encode()
    output=bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n');offsets=[0]
    for index,body in enumerate(objects,1):
        offsets.append(len(output));output+=str(index).encode()+b' 0 obj\n'+body+b'\nendobj\n'
    xref=len(output);output+=b'xref\n0 '+str(len(objects)+1).encode()+b'\n0000000000 65535 f \n'
    for offset in offsets[1:]:output+=f'{offset:010d} 00000 n \n'.encode()
    output+=b'trailer\n<< /Size '+str(len(objects)+1).encode()+b' /Root '+str(catalog).encode()+b' 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'
    return bytes(output)

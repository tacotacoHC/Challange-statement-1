"""Strict, source-only parsing for messages forwarded by an external sender."""
import datetime as dt
import re

from remote_entries import RemoteEntryError, submit_entry

MARKER = '#SITEBRIEF'
FIELDS = {'ref', 'project', 'contributor', 'location', 'date', 'time', 'discipline'}
REQUIRED = ('ref', 'contributor', 'location', 'date', 'time')
LIMITS = {'ref':100, 'project':180, 'contributor':120, 'location':180, 'discipline':100}


def parse_message(message, project):
    if not isinstance(message, str) or not message.strip():
        raise RemoteEntryError('Supply the original formatted message as a text value.')
    if len(message) > 30000:
        raise RemoteEntryError('Messages must be 30,000 characters or fewer.', 413)
    lines = message.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    while lines and not lines[0].strip(): lines.pop(0)
    if not lines or lines.pop(0).strip().upper() != MARKER:
        raise RemoteEntryError('Start the message with #SITEBRIEF on its own line.')
    values = {}
    body = None
    for index, line in enumerate(lines):
        if not line.strip(): continue
        label, separator, value = line.partition(':')
        key = label.strip().casefold()
        if not separator:
            raise RemoteEntryError('Use Field: value headers, then Update: followed by the site note.')
        if key == 'update':
            body = '\n'.join([value.lstrip(), *lines[index + 1:]]).strip()
            break
        if key not in FIELDS:
            raise RemoteEntryError('Unknown message header: '+label.strip()+'. Put action details, Owner: and Deadline: after Update:.')
        if key in values: raise RemoteEntryError('Message header appears twice: '+label.strip()+'.')
        values[key] = value.strip()
    missing = [name.title() for name in REQUIRED if not values.get(name)]
    if missing: raise RemoteEntryError('Add these message headers: '+', '.join(missing)+'.')
    if not body: raise RemoteEntryError('Add Update: as the final header, followed by the site note.')
    for key, limit in LIMITS.items():
        if len(values.get(key, '')) > limit:
            raise RemoteEntryError(key.title()+' must be '+str(limit)+' characters or fewer.')
    reference = values['ref']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{2,99}', reference):
        raise RemoteEntryError('Ref must be 3–100 letters, numbers, dots, colons, dashes or underscores. Use a new Ref for each entry.')
    if values.get('project') and values['project'].casefold() not in {project['id'].casefold(), project['name'].casefold()}:
        raise RemoteEntryError('The message Project does not match the project selected in Remote entries.', 403)
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', values['date']): raise ValueError()
        dt.date.fromisoformat(values['date'])
    except ValueError: raise RemoteEntryError('Date must be a valid YYYY-MM-DD date.')
    try:
        if not re.fullmatch(r'\d{2}:\d{2}', values['time']): raise ValueError()
        dt.time.fromisoformat(values['time'])
    except ValueError: raise RemoteEntryError('Time must be a valid HH:MM time in Singapore time.')
    entry = {'projectId':project['id'], 'type':'chat', 'contributor':values['contributor'],
             'location':values['location'], 'date':values['date'], 'time':values['time'],
             'discipline':values.get('discipline') or 'General', 'text':body}
    return entry, reference


def submit_message(workspace, payload, config):
    if not isinstance(payload, dict) or set(payload) not in ({'message'}, {'text'}):
        raise RemoteEntryError('Send JSON with one message (or text) field containing the original formatted message.')
    original = payload.get('message') if 'message' in payload else payload.get('text')
    entry, reference = parse_message(original, workspace.project(config['projectId']))
    # The source Ref is stable across automation retries; no generated IDs on retry.
    result, status = submit_entry(workspace, entry, config, 'message:'+reference,
                                  source={'channel':'Forwarded message', 'reference':reference, 'message':original})
    return {**result, 'kind':'site-update', 'messageRef':reference, 'reviewRequired':True}, status

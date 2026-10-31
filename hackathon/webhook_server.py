#!/usr/bin/env python3
"""Narrow task receiver intended to be exposed through ngrok."""
import argparse
import json
import traceback
from pathlib import Path
from urllib.parse import urlsplit
from remote_entries import RemoteEntryError, authorize, entry_context, submit_entry, settings
from message_entries import submit_message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from server import ApiError, Workspace, now

MAX_BODY = 64 * 1024
MAX_ENTRY_BODY = 48 * 1024 * 1024
PUBLIC = Path(__file__).resolve().parent / "public"
FIELDS = {'title', 'description', 'projectId', 'location', 'owner', 'deadline',
          'priority', 'status', 'contractor'}


class Handler(BaseHTTPRequestHandler):
    workspace = None

    def log_message(self, format, *args):
        # Do not log headers or URL query strings.
        pass

    def respond(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/webhooks/updates':return self.receive_entry()
        if path not in ('/webhooks/tasks', '/tasks'):
            return self.respond(404, {'error': 'Endpoint not found.'})
        try:
            if self.headers.get('Transfer-Encoding'):
                raise ApiError('Transfer-Encoding is not supported; send Content-Length.', 400)
            try:
                length = int(self.headers.get('Content-Length', ''))
            except ValueError:
                raise ApiError('A valid Content-Length is required.', 411)
            if length < 0: raise ApiError('Invalid Content-Length.')
            if length > MAX_BODY: raise ApiError('Payload exceeds 64 KB.', 413)
            content_type = self.headers.get_content_type()
            if content_type not in ('application/json', 'text/plain'):
                raise ApiError('Send Content-Type: application/json, or text/plain for a formatted message.', 415)
            config = authorize(self.workspace.store, self.headers.get('Authorization', '')) if content_type == 'text/plain' else None
            self.connection.settimeout(30)
            raw = self.rfile.read(length)
            if len(raw) != length: raise ApiError('The request was interrupted. Retry the same entry.')
            try:
                payload = {'message':raw.decode('utf-8')} if content_type == 'text/plain' else json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise ApiError('Payload must be valid JSON or UTF-8 message text.')
            if not isinstance(payload, dict): raise ApiError('Payload must be a JSON object.')
            if 'message' in payload or 'text' in payload:
                config = config or authorize(self.workspace.store, self.headers.get('Authorization', ''))
                result, status = submit_message(self.workspace, payload, config)
                print(json.dumps({'receivedAt':now(), 'entryId':result['id'], 'projectId':result['projectId'], 'duplicate':result['duplicate']}), flush=True)
                return self.respond(status, result)
            unknown = set(payload) - FIELDS
            if unknown: raise ApiError('Unknown fields: ' + ', '.join(sorted(unknown)))
            if any(not isinstance(v, str) for v in payload.values()):
                raise ApiError('All fields must be strings.')
            data = dict(payload)
            data.setdefault('projectId', 'workspace')
            with self.workspace.store.lock:
                action = self.workspace.action(None, data, return_action=True)
            print(json.dumps({'receivedAt':now(), 'actionId':action['id'], 'projectId':action['projectId']}), flush=True)
            self.respond(201, {'id': action['id'], 'projectId': action['projectId'], 'status': action['status']})
        except (ApiError, RemoteEntryError) as exc:
            self.respond(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except TimeoutError:
            self.respond(408, {'error':'The request timed out. Retry the same message reference.'})
        except Exception:
            traceback.print_exc()
            self.respond(500, {'error': 'Unable to process the task.'})

    def do_PATCH(self):
        path = urlsplit(self.path).path
        collections = {'/tasks':'actions', '/webhooks/tasks':'actions',
                       '/issues':'issues', '/webhooks/issues':'issues'}
        collection = collections.get(path)
        if not collection:
            return self.respond(404, {'error': 'Endpoint not found.'})
        try:
            if self.headers.get('Transfer-Encoding'):
                raise ApiError('Send Content-Length instead of Transfer-Encoding.')
            try: length = int(self.headers.get('Content-Length', ''))
            except ValueError: raise ApiError('A valid Content-Length is required.', 411)
            if length <= 0: raise ApiError('Send a JSON object with keywords and status.')
            if length > MAX_BODY: raise ApiError('Payload exceeds 64 KB.', 413)
            if self.headers.get_content_type() != 'application/json':
                raise ApiError('Send Content-Type: application/json.', 415)
            self.connection.settimeout(30)
            raw = self.rfile.read(length)
            if len(raw) != length: raise ApiError('The request was interrupted.')
            try: payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError): raise ApiError('Payload must be valid JSON.')
            if not isinstance(payload, dict): raise ApiError('Payload must be a JSON object.')
            if set(payload) - {'keywords', 'status', 'projectId'}: raise ApiError('Only keywords, status and projectId are supported.')
            keywords = payload.get('keywords')
            if isinstance(keywords, str): keywords = keywords.split()
            if not isinstance(keywords, list) or not keywords or len(keywords) > 20 or any(not isinstance(k, str) or not k.strip() or len(k) > 100 for k in keywords):
                raise ApiError('Supply one to 20 non-empty keywords as a string or an array of strings.')
            keywords = [k.strip().casefold() for k in keywords]
            status = payload.get('status')
            if not isinstance(status, str) or status.casefold().strip() not in {'open', 'in progress', 'resolved'}:
                raise ApiError('Status must be Open, In Progress or Resolved.')
            status = {'open':'Open', 'in progress':'In Progress', 'resolved':'Resolved'}[status.casefold().strip()]
            projectid = payload.get('projectId', 'workspace')
            if not isinstance(projectid, str) or not projectid.strip():
                raise ApiError('projectId must be a non-empty string.')
            with self.workspace.store.lock:
                self.workspace.project(projectid)
                matches = []
                for item in self.workspace.store.all(collection):
                    if item.get('projectId') != projectid or item.get('review') == 'rejected': continue
                    content = ' '.join(str(item.get(key) or '') for key in ('title', 'description', 'location')).casefold()
                    if all(keyword in content for keyword in keywords): matches.append(item)
                if not matches: raise ApiError('No matching record found in this project.', 404)
                if len(matches) != 1: raise ApiError('Multiple records match. Add more specific keywords.', 409)
                record = matches[0]
                if collection == 'actions':
                    self.workspace.action(record['id'], {'status':status}, return_action=True)
                else:
                    self.workspace.issue(record['id'], {'status':status})
            print(json.dumps({'receivedAt':now(), 'recordId':record['id'], 'projectId':projectid, 'collection':collection}), flush=True)
            return self.respond(200, {'id':record['id'], 'projectId':projectid, 'status':status, 'kind':collection[:-1]})
        except (ApiError, RemoteEntryError) as exc:
            return self.respond(exc.status, {'error':exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except TimeoutError:
            return self.respond(408, {'error':'The request timed out. Retry the update.'})
        except Exception:
            traceback.print_exc()
            return self.respond(500, {'error':'Unable to update the record.'})

    def receive_entry(self):
        try:
            config=authorize(self.workspace.store,self.headers.get('Authorization',''))
            if self.headers.get('Transfer-Encoding'):raise RemoteEntryError('Send Content-Length instead of Transfer-Encoding.')
            try:length=int(self.headers.get('Content-Length',''))
            except ValueError:raise RemoteEntryError('A valid Content-Length is required.',411)
            if length<=0:raise RemoteEntryError('Enter a site update.')
            if length>MAX_ENTRY_BODY:raise RemoteEntryError('The entry is too large. Send fewer or smaller files.',413)
            if self.headers.get_content_type()!='application/json':raise RemoteEntryError('Send Content-Type: application/json.',415)
            self.connection.settimeout(45)
            try:payload=json.loads(self.rfile.read(length))
            except (ValueError,UnicodeDecodeError):raise RemoteEntryError('The entry could not be read. Check the JSON and retry.')
            result,status=submit_entry(self.workspace,payload,config,self.headers.get('Idempotency-Key',''))
            # Original notes, captions, recordings and the access code never go into terminal logs.
            print(json.dumps({'receivedAt':now(),'entryId':result['id'],'projectId':result['projectId'],'duplicate':result['duplicate']}),flush=True)
            self.respond(status,result)
        except (ApiError,RemoteEntryError) as exc:self.respond(exc.status,{'error':exc.message})
        except (BrokenPipeError,ConnectionResetError):pass
        except TimeoutError:self.respond(408,{'error':'The upload timed out. Retry using the same submission reference.'})
        except Exception:
            traceback.print_exc();self.respond(500,{'error':'Unable to save this entry. Keep the form open and retry.'})

    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/entry/health':return self.respond(200,{'app':'SiteBrief entry receiver','entries':True,'messages':True,'version':2})
        if path=='/entry/context':
            try:return self.respond(200,entry_context(self.workspace,authorize(self.workspace.store,self.headers.get('Authorization',''))))
            except (ApiError,RemoteEntryError) as exc:return self.respond(exc.status,{'error':exc.message})
        assets={'/':'entry-form.html','/submit':'entry-form.html','/entry-assets/entry-form.css':'entry-form.css','/entry-assets/entry-form.js':'entry-form.js','/favicon.svg':'favicon.svg'}
        filename=assets.get(path)
        if filename:
            disk=PUBLIC/filename
            if not disk.is_file():return self.respond(503,{'error':'The entry form is not installed yet.'})
            body=disk.read_bytes();content_type='text/html' if filename.endswith('.html') else 'text/css' if filename.endswith('.css') else 'application/javascript' if filename.endswith('.js') else 'image/svg+xml'
            self.send_response(200);self.send_header('Content-Type',content_type+'; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");self.end_headers();self.wfile.write(body);return
        self.respond(404, {'error': 'Endpoint not found.'})


def main():
    parser = argparse.ArgumentParser(description='Run the SiteBrief task webhook receiver')
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    Handler.workspace = Workspace()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    config=settings(Handler.workspace.store);config['receiverPort']=server.server_port;Handler.workspace.store.put('remoteEntrySettings',config)
    print(f'Task receiver listening on 127.0.0.1:{server.server_port}; workspace: {Handler.workspace.store.directory}', flush=True)
    print(f'Remote entry form: http://127.0.0.1:{server.server_port}/submit; access code is available in the local dashboard Remote entries dialog.',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

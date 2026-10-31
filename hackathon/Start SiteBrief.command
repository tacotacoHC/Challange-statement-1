#!/bin/zsh
# Start the durable local workspace. Keep its server Terminal window open while using SiteBrief.
cd -- "${0:A:h}" || exit 1

# Reuse an existing SiteBrief server instead of starting a conflicting second copy.
if python3 - "$@" <<'PY'
import argparse,json,urllib.request
parser=argparse.ArgumentParser(add_help=False)
parser.add_argument('--port',type=int,default=8000)
parser.add_argument('--host',default='127.0.0.1')
args,_=parser.parse_known_args()
host='127.0.0.1' if args.host=='0.0.0.0' else args.host
base=f'http://{host}:{args.port}'
for endpoint in ['/api/health','/api/state']:
    try:
        with urllib.request.urlopen(base+endpoint,timeout=1) as response:
            data=json.load(response)
        if data.get('app')=='SiteBrief' or all(key in data for key in ('projects','updates','reports','actions')):
            print('SiteBrief is already running at '+base)
            raise SystemExit(0)
    except (OSError,ValueError):
        continue
raise SystemExit(1)
PY
then
  print -r -- "Keep the original server Terminal window open while adding entries."
  read "?Press Enter to close this launcher window."
  exit 0
fi

print -r -- "Starting SiteBrief. Keep this window open while adding entries."
print -r -- "Open the URL printed below in your browser. Stop with Control+C."
python3 server.py "$@"
app_exit_code=$?
if (( app_exit_code != 0 )); then
  print -r -- "SiteBrief could not start. Check the message above; another app may already be using the port."
  read "?Press Enter to close this window."
fi
exit "$app_exit_code"

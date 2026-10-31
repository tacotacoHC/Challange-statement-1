#!/bin/zsh
cd -- "${0:A:h}" || exit 1
print -r -- "Starting SiteBrief's entry receiver. Keep this window open."
print -r -- "In another Terminal, run: ngrok http 8001"
print -r -- "Then use Site Updates > Remote entries in the local dashboard to copy an invitation."
python3 webhook_server.py "$@"
app_exit_code=$?
if (( app_exit_code != 0 )); then
  print -r -- "The receiver could not start. Check the message above; if an older receiver is running, stop it with Control+C and launch this version."
  read "?Press Enter to close this window."
fi
exit "$app_exit_code"

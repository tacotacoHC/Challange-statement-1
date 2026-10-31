# SiteBrief

A locally runnable construction site reporting workspace. Save site notes, pasted chats, photographs and recordings; review evidence-linked daily findings; manage issues and actions; approve and export daily reports. SiteBrief deliberately does not estimate construction progress from before-and-after imagery.

## Launch

Requires Python 3.10 or newer. No package installation is required. The IFC viewer uses pinned JavaScript/WASM libraries bundled with the app; the Python server uses the standard library.

```sh
cd sitebrief
python3 server.py
```

Open **http://localhost:8000**. Stop with Ctrl+C. If the port is occupied, use `python3 server.py --port 8001`. Data is saved on disk in `data/`, including a SQLite database and original uploads, and survives refreshes and server restarts. Set `SITEBRIEF_DATA_DIR` to use a different workspace. The server binds to localhost by default; this prototype has no production authentication or multi-tenant account system.

## If saving shows “Failed to fetch”

SiteBrief needs its Python app server running while you use it. An already-open browser tab can remain visible after the server stops, but it cannot save new entries. Saving notes, chats and manual evidence descriptions does not require an AI API key.

On macOS, double-click **Start SiteBrief.command** in the project folder, or run `python3 server.py` as shown above. Keep that Terminal window open and use its printed local URL (normally `http://127.0.0.1:8000`). Opening `public/index.html` directly or using a separate static preview server cannot save data. If a connection fails while entering an update, the form stays open so it can be retried after reconnecting. AI processing failures are reported separately, and the uploaded evidence is retained.

`GET /api/health` returns a small SiteBrief health response for connection checks; it does not expose credentials or workspace records.

## Remote site entries through ngrok

Contributors can open a mobile-friendly entry form to send notes, pasted chats, photos and uploaded recordings. Submissions are stored in the same local SQLite workspace as the dashboard and appear in **Site Updates**; the active dashboard checks for new entries every 10 seconds. This workflow does not require a Google or Gemini key.

1. Start the dashboard with `python3 server.py` (or **Start SiteBrief.command**).
2. In a second Terminal, run `python3 webhook_server.py` (or **Start Remote Entries.command**).
3. Keep your configured ngrok agent running against the receiver: `ngrok http 8001`.
4. Open **Site Updates → Remote entries** in the local dashboard. Select the project, confirm the public ngrok base URL, and save the settings. An agent using the default local inspection port 4040 is detected automatically; otherwise paste its HTTPS URL.
5. Choose **Copy invitation** and share that link with the contributor. It opens `/submit` with an entry access code in a URL fragment; the form removes the fragment after reading it. Alternatively, share `/submit` and the entry code separately. The entry code is separate from both an ngrok authtoken and an AI API key.
6. Contributors supply their name, location, date/time and source content. They can preview photos and play recordings before submitting. A receipt reference confirms that the entry has been saved. The officer can then review it, generate a report, and link extracted issues to IFC elements as usual.

Keep all three processes running. When updating the Python source, restart the dashboard and receiver once. The receiver serves the submission page and its assets, selected-project context after authentication, and the update submission endpoint. It does not serve the dashboard, reports, saved evidence files or IFC models. Use the receiver port for the tunnel.

Entry intake uses a persistent shared code stored in the private workspace settings, excluded from `/api/state`. Pause intake or create a new code from the local dialog to revoke invitations. Changing the selected project automatically creates a new code, so old invitations do not gain access to another project. Contributors' names are self-reported; submissions and model placements still need officer review.

The form accepts JPG/PNG/WebP/GIF photos (12 MB each), MP3/WAV/M4A/OGG/WebM recordings (25 MB each), up to six files and 34 MB combined per entry. Invalid formats and oversized requests receive clear errors. Original files and supplied captions/transcripts are retained; remote intake does not run paid media analysis. Add a manual caption or transcript when available. The receiver logs only entry IDs/times and project IDs for this workflow, not notes, files or access codes. ngrok's traffic inspector can still record HTTP content under your ngrok configuration.

Programmatic senders can POST JSON to `/webhooks/updates` with `Authorization: Bearer ENTRY_CODE`, `Content-Type: application/json`, and a unique `Idempotency-Key` (8–128 allowed letters/numbers/dots/colons/dashes/underscores). Entry fields match the public form: `projectId`, `type` (`note`, `chat`, `photo`, `audio`), `contributor`, `location`, `date` (`YYYY-MM-DD`), `time` (`HH:MM`, Singapore time), `discipline`, `text`, `caption`, `transcript`, and `files` (`name`, `mime`, base64 `data`). An omitted project defaults to the configured shared project. Unknown fields and other projects are rejected. Repeating the same successful request with the same idempotency key returns the original receipt without creating a duplicate; reusing a key for changed content returns `409`.

The browser form keeps its entry and submission reference when a connection fails so an unchanged retry is safe. An interrupted upload must remain in the same open browser tab to retain selected files. A new entry receives a new reference.

### iMessage or other messages forwarded by your API sender

Your existing iMessage-to-HTTP sender can use **`POST /tasks` or `POST /webhooks/tasks`** on the same ngrok URL. SiteBrief receives the forwarded request; it does not connect to an Apple ID or read Messages. No AI key is needed. The sender must be configured separately to forward the message text. This integration does not send replies to iMessage.

Two payloads are supported:

- Your existing JSON such as `{"title":"Inspect the pump","owner":"Sam","priority":"High"}` creates an action with the existing task behavior below. `/tasks` is an alias for that endpoint.
- A formatted message, sent as `{"message":"THE COMPLETE MULTILINE TEXT"}` (or a single `text` field), creates a **site update** retaining its source evidence for officer review. It does not immediately create confirmed issues, assignments or an approved report. Generate and review a daily report to propose follow-up actions.

For formatted messages, set `Content-Type: application/json` and `Authorization: Bearer ENTRY_CODE`. Obtain the entry code from **Site Updates → Remote entries → Copy entry code**. This uses the same selected project, pause setting and code rotation as the public form. Keep the code in your sender configuration, outside the message text. `text/plain; charset=utf-8` with the same Authorization header is also supported.

Use this format, replacing the fictional details:

```text
#SITEBRIEF
Ref: MSG-20261031-pump-001
Contributor: Alex Morgan
Location: Basement - Pump room
Date: 2026-10-31
Time: 14:00
Discipline: M&E
Update:
Pump P-02 is not starting. Please arrange an inspection.
Owner: Sam
Deadline: 2026-11-01
```

`Ref`, `Contributor`, `Location`, `Date`, `Time`, and the `Update` body are required. `Discipline` is optional. An optional `Project` header must exactly match the shared project's name or ID (case-insensitive); omitting it uses the selected shared project. Dates use `YYYY-MM-DD` and times use `HH:MM` in Singapore time. `Update:` must be the final header: every subsequent line is original source content, including any explicit owner and deadline. Omit owner/deadline lines when unknown; SiteBrief does not assign the contributor automatically or invent a deadline. Names are self-reported by the sender, not independently authenticated by Apple.

Use a unique `Ref` per separate entry (3–100 letters/numbers/dots/colons/underscores/dashes). A retry of the same message with the same Ref returns the original receipt with `duplicate: true`, even across the two endpoint aliases or after a restart. Reusing a Ref for different content returns `409` and does not overwrite the source or reviewed records. The sender should retain the exact message when retrying. Malformed headers, missing required fields and incorrect project names receive explicit errors without saving an update. Limits are 64 KB per HTTP request and 30,000 message characters. Photo/audio attachments are not imported by this text-message adapter; use the public entry form for those files.

For a quick test, save the example as `message.txt`, configure `SITEBRIEF_ENTRY_CODE` in your shell, then run:

```sh
curl -i 'https://YOUR-NGROK-DOMAIN/tasks' \
  -H 'Content-Type: text/plain; charset=utf-8' \
  -H "Authorization: Bearer $SITEBRIEF_ENTRY_CODE" \
  --data-binary @message.txt
```

A new message returns `201` with `kind: "site-update"`, an update ID, receipt reference and `reviewRequired: true`; an unchanged retry returns `200`. Find it in **Site Updates** under the shared project, then open **Source evidence → Original forwarded message** to inspect the complete original. All content stays source data and cannot command the app to approve records. The Remote entries dialog includes **Copy example message** and **Copy /tasks URL** controls for configuring your sender. Replace the fictional details in the copied example before sending it.

### Existing task integration

Run the dashboard and this separate receiver on the same machine, using the same `SITEBRIEF_DATA_DIR` if set. The existing task endpoint remains available alongside the new entry form. Use the receiver port for ngrok; the dashboard has no production authentication. The receiver binds to localhost. Task creation and keyword status updates have no authentication: anyone who can reach these endpoints can create tasks or change matching action and issue statuses while the tunnel is running. Formatted message intake requires the entry code.

In a second terminal, start the receiver:

```sh
python3 webhook_server.py
```

In a third terminal, run `ngrok http 8001`. If needed, first configure your ngrok account and authtoken using ngrok's setup instructions. Give the sender the resulting HTTPS URL followed by `/webhooks/tasks`. Keep all three processes running. The ngrok URL may change after restarting unless your account has a fixed domain.

The sender can test with:

```sh
curl -i 'https://YOUR-NGROK-DOMAIN/webhooks/tasks' \
  -H 'Content-Type: application/json' \
  -d '{"title":"Inspect the pump","description":"Check the reported leak","owner":"Sam","priority":"High","deadline":"2026-12-01","projectId":"workspace"}'
```

The receiver returns `201` and the new action ID. `title` is required; `projectId` defaults to `workspace`. Optional string fields are `description`, `location`, `owner`, `deadline` (YYYY-MM-DD), `priority` (Low, Medium, High, Urgent or Critical), `status` (Open, In Progress or Resolved), and `contractor`. Other fields are rejected. For another project, obtain its ID from the local `/api/state` response. The dashboard refreshes automatically approximately every 10 seconds while it is visible and no form is being edited. Select the corresponding project to see the new record under **Issues & Actions → Actions**, in dashboard activity, and in **Recently received API actions**. The dashboard and tracker also link to projects with open API actions. The task endpoint creates an action, not an issue; a formatted message creates a site update for later report review. Both processes must use the same `SITEBRIEF_DATA_DIR`.

To update an existing record, send a `PATCH` request to `/tasks` or `/webhooks/tasks` for an action, or `/issues` or `/webhooks/issues` for an issue:

```sh
curl -i -X PATCH 'https://YOUR-NGROK-DOMAIN/webhooks/tasks' \
  -H 'Content-Type: application/json' \
  -d '{"keywords":["pump", "P-02"],"status":"In Progress"}'
```

Status updates do not require an entry code. `projectId` defaults to `workspace`; specify another existing project ID to match records in that project. `keywords` can be an array of phrases or a whitespace-separated string. Every term must occur, case-insensitively, in the title, description or location. The request changes only a single matching record; no match returns `404` and multiple matches return `409`, with no changes. Add more specific keywords and retry. Supported statuses are `Open`, `In Progress` and `Resolved` (case-insensitive). Updates preserve the record's review decision and appear in the dashboard after the next refresh. A successful response includes the record ID, project ID, kind and status. Restart the receiver after updating its source.

The receiver logs saved record IDs, project IDs and receipt times without message bodies, task descriptions or authorization headers. Earlier receiver versions logged task bodies; restart once to activate the current behavior. ngrok's local inspector at `http://127.0.0.1:4040` can still contain HTTP content and request headers under your ngrok configuration. Logs are not stored by the app after the receiver exits unless you redirect them to a file. Requests are limited to 64 KB. The legacy task endpoint does not deduplicate retries: a repeated successful POST creates another action. The new site-update endpoint uses the idempotency mechanism described above. Stop ngrok and the receiver with Ctrl+C when finished.

## Demonstration

1. Select **Load demo project**. The samples are fictional and use the current Singapore date. Loading them again does not duplicate evidence.
2. Open **Site Updates**, inspect the completed work, delivery delay, unassigned equipment issue, urgent safety report and conflicting facade updates. The photo example is a fictional supporting illustration; recordings can be added through the upload form.
3. Generate a daily report for the demo project and date. Open the evidence attached to a finding and compare the original source with the summary.
4. Edit a finding, then accept or reject the generated findings and action suggestions. Missing owners and dates stay empty until a reviewer supplies them.
5. In **Issues & Actions**, assign an unassigned action, set a deadline and move work through Open, In Progress and Resolved. Contractor acceptance and PM completion verification are recorded inside the workspace; no external messages are sent.
6. Return to **Daily Reports**, finish review, enter the reviewer name and approve. Download the approved PDF. Editing approved content returns the report to Draft and requires approval again.
7. Refresh the browser or restart the server to confirm that changes persist. Regenerating preserves reviewed findings and does not duplicate generated issues or tasks.

## BIM and IFC issue flags

Open **BIM & IFC** to inspect a model and connect reported issues, follow-up actions or original updates to its elements. This feature works without a Google or Gemini key.

1. Select **Load BIM demo** on the BIM page. This loads the fictional site updates, generates an unreviewed report when issues are absent, and opens the demo IFC model. The small fictional IFC4 model represents two blocks, four Block A levels and a basement. Its named spaces and products match the sample update locations; it is design context, not evidence of actual construction condition.
2. Alternatively, upload a STEP `.ifc` file (up to 25 MB). The original model is saved locally. Its product names, IFC type, GlobalId, express ID and spatial structure are indexed. Identical uploads in the same project reuse the saved model.
3. Inspect the model in 3D or use the element list. Select the affected product or a location/space, open the related issue, action or update, and confirm the link as an officer. An express ID is local to one model; saved links also retain that model and its GlobalId.
4. Review suggested matches before confirming them. Suggestions use reported location, element name, tag or GlobalId. Ambiguous or missing references remain unmatched. A location-only match is not a verified match to a physical component.
5. Inspect a flag to open its linked record and source evidence. Owner, deadline, priority and status come from the existing saved tracker. BIM mapping does not accept a generated issue or certify completion. Reassign or unlink a flag if the target needs correction.

The viewer uses [Three.js](https://threejs.org/) and [web-ifc](https://github.com/ThatOpen/engine_web-ifc). Vendored versions and licences are documented under `public/vendor/`. Uploaded files remain in the local workspace; model viewing and deterministic matching do not call an external AI API. The metadata index is a focused STEP reader, while web-ifc performs the geometry parsing. Complex or unsupported geometry can fail to render; the saved original and indexed element list remain available. Flags are workspace annotations and are not written into the original IFC file or exported as BCF.

The demo can be regenerated with `python3 scripts/create_demo_ifc.py`. It was parsed by web-ifc as **43 standard meshes plus six space meshes**, confirming all 49 geometric products have real IFC geometry. The IFC model and an officer-confirmed pump flag were also rendered and checked in Chrome through native browser controls. Model viewing and placement were verified on desktop; mobile visual testing is described below.

## AI configuration

Without credentials, the app runs in explicitly labelled **Demo mode**. The fictional sample evidence has deterministic, source-linked findings. Other text updates can be organized conservatively, while arbitrary uploaded images and audio require manual captions and transcripts. Demo mode does not claim to inspect photograph quality, verify regulations, recognize completed work, or transcribe a recording.

Credentials stay on the server. Copy the variable names from `.env.example` and export them in the shell before launch; `.env` files are not loaded automatically. Do not put secrets in browser code or commit a `.env` file. API calls require network access.

**Google Gemini** is available as an optional provider using a Gemini Developer API key from Google AI Studio. To enable report extraction later:

```sh
export AI_PROVIDER=google
export GEMINI_API_KEY='your-key'
export GEMINI_TEXT_MODEL='gemini-3.8-flash'
python3 server.py
```

Set `GEMINI_VISION_MODEL` and/or `GEMINI_AUDIO_MODEL` to a supported multimodal model, such as `gemini-3.8-flash`, to opt into image descriptions or recorded-audio transcripts. These media features stay in manual mode while their model variables are empty. This adapter uses the [Google Gemini REST API](https://ai.google.dev/api/generate-content), with the key in a server-side request header. `GOOGLE_API_KEY` is also accepted as a key alias.

The prototype sends inline media requests below 19 MB (including base64 data and prompts), conservatively within Google's modality-specific inline limits. Larger files remain saved and use manual captions/transcripts. Supported Google processing formats here are JPG/PNG/WebP and MP3/WAV/OGG; GIF/M4A/WebM uploads remain available as original evidence with manual descriptions. No file is marked analyzed or transcribed unless the configured request returns usable, complete content.

The existing **OpenAI** adapter remains available with `AI_PROVIDER=openai`, `OPENAI_API_KEY`, and its existing `OPENAI_TEXT_MODEL`, `OPENAI_VISION_MODEL`, and `OPENAI_TRANSCRIBE_MODEL` variables. Explicit `AI_PROVIDER=demo` keeps all processing local even if API keys are present. With no provider set, a Gemini key is selected first, then an OpenAI key; an explicitly selected provider never silently sends evidence to another provider. Missing keys or invalid provider settings use clearly labelled Demo mode.

Text extraction, optional image interpretation and audio transcription are isolated in `ai_service.py`. Inputs are passed as source data, not instructions. Both adapters use the same evidence-reference and explicit-assignment validation. Generated text and media interpretation remain subject to officer review. Failed processing preserves uploads and reviewed work and falls back to a labelled source-based report. Successful unchanged extractions are cached; failures remain retryable. Switching providers preserves matched reviewed records.

**Where AI may help:** turn varied site chats and notes into a draft, transcribe voice updates, suggest issue groupings, and point out missing or conflicting information. The tracker, date calculations, evidence storage, approvals, and export work without AI. Try representative real-world updates and compare preparation time, corrections required, and missed issues against a manually prepared report. Keep photo compliance, contractor responsibility, final completion verification and overall schedule impact under human review. Start with text extraction, then enable transcription or image descriptions only if the results save review time.

No live Google requests have been tested without credentials; mocked request/response and failure-path tests verify the adapter wiring. Check the current [Google model availability](https://ai.google.dev/gemini-api/docs/models) when configuring a key later.

## Scope and review limits

Photo discipline tags, editable captions and source dates help organize evidence. Reported safety concerns are flagged for urgent human review; neither a photograph nor an AI summary establishes regulatory compliance. Defect responsibility, contractor acceptance, deadline reminders and PM verification are local workflow records. Date-based overdue indicators and reported delays support follow-up, but do not constitute a full project programme, critical-path analysis or automatic construction progress assessment. An external sender can forward structured messages to the inbound API described above. Outbound messaging and direct Apple inbox access are outside scope.

## Verification

The current suite passes 43 Python tests and two Node frontend checks. Forwarded-message checks cover both task URLs, legacy task compatibility, source preservation, explicit format errors, project/code controls, missing assignments, review persistence and retry deduplication. A synthetic message was also submitted through the existing ngrok tunnel: create returned 201, retry through the other alias returned 200 with the same ID, and changed content with the same Ref returned 409. The temporary test entry was removed afterward; existing user tasks were preserved. The external iMessage forwarding service itself is not configured or tested by SiteBrief.

Run the HTTP integration checks in an isolated temporary workspace:

```sh
python3 -m unittest discover -s tests -v
```

The Python tests exercise IFC upload validation, model and flag persistence, project-scoped element identities, conservative placement suggestions, explicit link review and unlinking, extraction-cache reuse and recovery, persistence, uploaded photo/audio playback, manual transcripts, evidence references, missing owner/deadline handling, review edits, action/report decision synchronization, idempotent reprocessing, resolved-task overdue counts, approval invalidation, PDF export, IFC upload/indexing, model-scoped links, conservative placement suggestions and unlinking. All approved PDF pages were rendered and visually inspected. Frontend rendering and connected handlers, including IFC demo loading, placement forms, source evidence, filter/reset behavior and stale-request protection, were checked in a JavaScript runtime with `node --test tests/frontend.cjs` (Node 20+; optional for running the app). Native Chrome visual checks verified IFC geometry, the desktop BIM layout, placement review and a saved issue marker. Automated checks also cover connected BIM actions, source evidence, filters and stale-request handling. Tablet/mobile visual verification remains a manual step; responsive breakpoints, keyboard handling and visible focus styles are implemented.

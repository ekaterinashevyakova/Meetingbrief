# MeetingBrief

A meeting-preparation assistant built by a team of four for Global AI Hackathon 2026.

Enter a company or person, a meeting time and an optional objective to prepare a concise briefing with recent developments, source references, discussion questions and information gaps.

## Hosted demo and development branch

[Open the hosted demo](https://meetingbrief.streamlit.app/)

The hosted application is deployed from `main` and currently uses fictional demonstration data. It does not automatically include changes made in other branches.

This branch, `katya/frontend-integration`, combines the updated frontend with the Tavily and Bifrost workflow. Demo mode and mode switching have been manually checked. The Live demo video was recorded from this branch using `openai/gpt-oss-20b` served by Groq through Bifrost.

## Demo and Live modes

| Mode | Behavior | Requirements |
| --- | --- | --- |
| Demo | Displays explicitly fictional examples; makes no research or model API calls | Python dependencies |
| Live | Calls Tavily, then requests a briefing from a model through Bifrost | Tavily key, reachable Bifrost gateway and a configured model provider |

Switching modes clears the previous briefing and resets the meeting form.

Bifrost is the model gateway. It does not supply model-provider credits or guarantee factual accuracy.

## Frontend features

- Company and person input modes with contextual labels and disambiguation guidance
- Meeting date, time, time zone and optional objective
- Required-name validation
- Structured events, questions, conversation starters and information gaps
- Expandable sources with supporting excerpts and reported publication/update dates
- Requested 30-day research window and collection-time display when supplied
- Markdown download, including demo warnings and source-date notes
- Full form and briefing reset
- Handling of empty results and generation failures

The Markdown download is not a guaranteed single-page PDF.

## How the workflow fits together

```text
Meeting form
    |
    +-- Demo --> demo_data.generate_briefing
    |
    +-- Live --> briefing_generator.generate_briefing
                     |
                     +-- tavily_search.search_sources
                     +-- model request through Bifrost
                     +-- JSON parsing and structural validation
    |
Briefing display and Markdown export
```

The Python application calls Tavily. Bifrost handles the subsequent model request.

## Run this branch locally

The initial frontend was developed with Python 3.14. Full Live compatibility must be checked in the team's test environment.

### 1. Get the development branch

For a new checkout:

```bash
git clone --branch katya/frontend-integration https://github.com/ekaterinashevyakova/Meetingbrief.git
cd Meetingbrief
```

If the repository is already on your computer, save your current work before switching branches:

```bash
git fetch origin
git switch katya/frontend-integration
git pull --ff-only origin katya/frontend-integration
```

### 2. Create and activate an environment

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Start the interface

```bash
python -m streamlit run app.py
```

Open the Local URL printed in the terminal, normally http://localhost:8501.

Select **Demo** for a no-key interface check. Enter meeting details and click **Generate briefing**. Keep the terminal running while using the app. To stop the server, press **Control+C** in that terminal.

## Additional setup for Live mode

### 1. Run Bifrost and configure a model provider

Follow the [Bifrost gateway setup](https://docs.getbifrost.ai/quickstart/gateway/setting-up) and [provider configuration](https://docs.getbifrost.ai/quickstart/gateway/provider-configuration) instructions.

Configure your own model-provider API key inside Bifrost. Set up gateway authentication compatible with the application's current Basic Authorization header.

The current model client sends provider-dependent generation options. Verify that the selected model supports these options; changing the model name alone may not be sufficient.

### 2. Configure local application secrets

Create a local `.env` file beside `app.py`:

```dotenv
TAVILY_API_KEY=REPLACE_WITH_YOUR_TAVILY_KEY
BIFROST_BASE_URL=http://localhost:8080/v1
BIFROST_BASIC_AUTH="Basic REPLACE_WITH_BASE64_OF_YOUR_GATEWAY_USERNAME_COLON_PASSWORD"
BIFROST_MODEL=REPLACE_WITH_YOUR_CONFIGURED_BIFROST_MODEL_ID
```

These are placeholders, not working credentials.

- `TAVILY_API_KEY` authenticates web research.
- `BIFROST_BASE_URL` points to the gateway API, including `/v1`.
- `BIFROST_BASIC_AUTH` authenticates to the gateway, not the model provider.
- `BIFROST_MODEL` identifies a model configured in Bifrost.
- The provider key belongs in Bifrost's configuration, not in the frontend.

The current client loads the project-root `.env` during the first Live import and allows it to override environment values. Restart Streamlit after changing configuration.

### 3. Check the full workflow

Ensure Bifrost is running, start Streamlit, select **Live** and submit a meeting. Check the returned sources and claims before treating the briefing as reliable.

A locally running Bifrost instance is not automatically reachable from Streamlit Community Cloud. On the hosted service, `localhost` refers to the cloud machine, not a teammate's laptop. A fully hosted Live version needs a reachable, authenticated gateway and separately configured secrets.

## Dates and evidence

- The requested window starts 30 days before the meeting.
- Available research must end no later than the meeting or the collection time.
- A source's `published_date` can be an estimate of publication or update time.
- An event's `date` describes the event itself and may be null.
- The publication date must not automatically become the event date.
- Source IDs and valid date formatting do not prove that a claim is supported.
- Historical briefings require careful exclusion of information unavailable at meeting time.

The frontend displays `researched_at` when present. The current copied generator does not yet forward the research timestamp to that field, so Live collection time may display **Not provided** until this mapping is added.

## Validation status and remaining work

Completed manual frontend checks include fictional result display, empty results, simulated generation errors, mode switching, Markdown export and source-date fallback display. See [TESTING.md](TESTING.md) for the recorded checks; it is being updated as integration progresses.

Still required:

- Reproduce the recorded Live workflow in a clean environment.
- Forward research collection metadata in every result path.
- Verify claim support, entity identity, event dates and research-window compliance.
- Test real source timestamps, missing dates and conflicting information.
- Investigate the reported loading indicator after Streamlit's built-in Stop action; reliable cancellation is not implemented or verified.
- Recheck reset, export and narrow-window layout after integration.
- Reproduce the setup on another team member's computer.
- Add the demo video link when it is available.

Do not treat the current structural validation as a factual verification system.

## Main files

| File | Responsibility |
| --- | --- |
| `app.py` | Form, Demo/Live selection, result display and export |
| `demo_data.py` | Fictional examples |
| `tavily_search.py` | Search, source processing and temporal filtering |
| `briefing_generator.py` | Research orchestration, model instructions and response parsing |
| `bifrost_client.py` | Model requests through Bifrost |
| `requirements.txt` | Python dependencies |
| [INTEGRATION.md](INTEGRATION.md) | Request/response contract and integration notes |
| [TESTING.md](TESTING.md) | Manual checks and pending validation |

## Team responsibilities

- **Katya:** frontend and user experience
- **Irina:** Tavily research and source processing
- **Marina:** Bifrost/model integration and briefing generation
- **Dilyara:** end-to-end testing, quality evaluation and reproducibility checks

## Credentials

Do not commit `.env`, provider keys, gateway credentials or Streamlit secrets. The repository ignores `.env` and `.streamlit/secrets.toml`.

Base64 encodes gateway credentials; it does not encrypt them. Replace credentials exposed in chats, recordings or repository history.

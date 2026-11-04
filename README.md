# MeetingBrief

An AI-powered meeting preparation assistant being developed for
Global AI Hackathon 2026 by a team of four.

## Live demo

https://meetingbrief.streamlit.app/ 

The demo uses fictional data. Live research and AI generation
are not connected yet.

## Project goal

Given a company or person and a scheduled meeting time, generate
a concise briefing covering relevant developments from the preceding
30 days, with citations, discussion questions, and information gaps.

## Current status

The Streamlit frontend is implemented with fictional demonstration data.

Implemented features:
- Company and person input modes with contextual field labels
- Meeting date, time, time zone and objective
- Required-name validation
- Structured briefing display with events and source excerpts
- Preservation and clearing of the last generated briefing
- Empty-result and generation-error handling

Live Tavily research and model access through Bifrost are not connected yet.

See INTEGRATION.md for the request and response format.
See TESTING.md for manual checks and remaining validation.

## Planned technology stack

- Python
- Streamlit — user interface
- Tavily — web research
- Bifrost — gateway for language model requests

## Run locally

Python 3.14 was used for the initial interface.

### 1. Clone the repository

    git clone https://github.com/ekaterinashevyakova/Meetingbrief.git
    cd Meetingbrief

### 2. Create a virtual environment

    python3 -m venv .venv

### 3. Activate it

macOS / Linux:

    source .venv/bin/activate

Windows PowerShell:

    .venv\Scripts\Activate.ps1

### 4. Install dependencies

    python -m pip install -r requirements.txt

### 5. Start the application

    python -m streamlit run app.py

Open http://localhost:8501 if the browser does not open automatically.
Keep the terminal running while using the application.
Press Ctrl+C in the terminal to stop it.

## Team responsibilities

1. User interface and user experience
2. Tavily integration and source processing
3. Bifrost integration and briefing generation
4. Application integration, testing, and setup documentation

## Next milestones

- Connect the interface to the research and generation workflow.
- Validate citations, dates, and entity relevance.
- Handle missing information and API failures.
- Prepare evaluation examples and the project demonstration.

## Credentials

The current interface demo does not require API keys.
Do not commit API keys, passwords, or local secrets to this repository.
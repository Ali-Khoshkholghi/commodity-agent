# Commodity Code Assistant

A conversational agent that helps small importers find the likely commodity
(tariff) code for a product. It asks the questions that decide classification,
checks the proposed code against the official UK Trade Tariff, and returns the
code, a confidence level, the standard duty rate and a link to the source.

Built with Google's [Agent Development Kit (ADK)](https://google.github.io/adk-docs/)
and Gemini on Vertex AI.

> **Disclaimer.** Output is a suggestion, not an official classification. For a
> binding decision, use HMRC's tariff tools or request an advance ruling.

## Features

- **Guided intake.** Asks 2-3 short questions first (material, function,
  destination, country of origin) instead of guessing from a one-line description.
- **Verified codes.** Confirms that the exact 10-digit code exists and is
  declarable in the UK Trade Tariff, and suggests nearby codes when it does not.
- **Sourced duty rates.** Reports the standard (third-country) duty with a link
  to the official page. Rates it cannot verify are labelled as unverified.
- **Calibrated confidence.** Confidence is capped in code, not only in the
  prompt: never "high" for an unverified code, or when a different tariff
  heading is also plausible.
- **Scoped behaviour.** Declines out-of-scope requests such as supplier
  sourcing or shipping advice.

## Example

Illustrative output for a UK import (a plastic protective phone case with a
built-in battery, from China):

```
Code: 3926909790 (Confidence: medium)
Why: Classified under plastic articles as the main function is a protective
phone case. As a composite product, protection gives it its essential
character over the secondary battery feature.
Duty: 6.00 %. Check the source page for any preferential rate for imports
from China.
Source: https://www.trade-tariff.service.gov.uk/commodities/3926909790
Could change if: heading 8507 applies if the item is judged to be primarily
an electric accumulator rather than a case.
```

## How it works

| Component | Type | Role |
|---|---|---|
| `commodity_code_assistant` | Root agent (Gemini) | Runs the intake, picks a candidate code, composes the answer |
| `lookup_uk_tariff` | Function tool | Calls the public UK Trade Tariff API: existence, declarability, standard duty, nearby codes |
| `duty_search` | Sub-agent with Google Search, exposed as a tool | Checks EU (TARIC) codes; often returns "unverified" |
| `validate_and_save_code` | Function tool with session state | Validates code format, applies the confidence caps, stores proposals in session state |

Request flow: intake questions, candidate code, official lookup (at most two),
validation and confidence cap, formatted answer.

## Requirements

- Python 3.10+
- A Google Cloud project with the Vertex AI API enabled
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) with
  Application Default Credentials

## Setup

```bash
git clone https://github.com/Ali-Khoshkholghi/commodity-agent.git
cd commodity-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com

cp commodity_agent/.env.example commodity_agent/.env
# edit commodity_agent/.env and set GOOGLE_CLOUD_PROJECT
```

## Run

```bash
adk web .          # browser UI; select "commodity_agent"
adk run commodity_agent   # terminal chat
```

Check the UK lookup on its own:

```bash
python commodity_agent/agent.py 9503003990
```

## Configuration

Set in `commodity_agent/.env` (see `.env.example`):

| Variable | Description |
|---|---|
| `GOOGLE_GENAI_USE_ENTERPRISE` | `1` to use Vertex AI |
| `GOOGLE_CLOUD_PROJECT` | Your Google Cloud project ID |
| `GOOGLE_CLOUD_LOCATION` | Region, `global` by default |

The model is set in `commodity_agent/agent.py` (`MODEL`). Model availability and
retirement dates change; check the Vertex AI model versions page before
deploying.

## Project structure

```
commodity_agent/
  agent.py          agents and tools
  __init__.py
  .env.example      configuration template
requirements.txt
```

## Limitations

- Duty verification is UK only. EU checks rely on web search and usually
  return "unverified".
- Preferential rates by country of origin are not calculated; the answer points
  to the source page.
- Classification of composite products is a judgement call; the agent flags
  these as medium confidence at most.

## Cost

Everything runs locally. Only Gemini calls (and any Google Search grounding)
are billed to your Google Cloud project; the UK Trade Tariff API is free.
Nothing is deployed, so nothing bills while idle.

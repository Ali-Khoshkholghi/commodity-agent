# Commodity-code assistant (learning project: Vertex AI + ADK)

A small conversational agent built with Google's Agent Development Kit (ADK)
and Gemini on Vertex AI. A seller describes a product; the agent asks 2-3
clarifying questions, proposes a commodity (tariff) code, checks it against the
official UK Trade Tariff, and returns the code, a confidence level, the
standard duty and a source link.

It is a learning project. Output is a suggestion, not an official
classification. For anything binding, use HMRC's tariff tools or request an
advance ruling.

## Run it

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    gcloud auth application-default login      # once
    adk web .                                  # then pick "commodity_agent"

Config lives in `commodity_agent/.env` (project, `global` location, Vertex on).
Model: `gemini-3.6-flash`. No sampling parameters are set on purpose.

Quick live test of the UK lookup tool on its own:

    python commodity_agent/agent.py 9503003990

## How it works (all in `commodity_agent/agent.py`)

1. **Intake behaviour**: instructions force clarifying questions first
   (material, function, destination, origin) and keep the agent in scope.
2. **`lookup_uk_tariff`** (function tool): calls the free UK Trade Tariff API
   to confirm the exact 10-digit code exists and is declarable, and reads the
   standard duty. If not found, it returns nearby declarable codes.
3. **`duty_search`** (sub-agent with Google Search, wrapped as a tool): used
   for EU/TARIC; weak at verifying official pages, so it often says unverified.
4. **`validate_and_save_code`** (function tool + session state): checks the
   code's shape and saves it to state. Confidence is capped **in code**:
   never "high" if the code was not verified on an official site, or if a
   different heading is also plausible.

## What this project taught

- Prompts steer; code enforces. Rules the model must not break (the confidence
  cap) belong in a tool, not only in the instructions.
- "The code exists" and "the code is right" are different questions. A lookup
  answers the first; the second is judgement and should say "medium".
- Google Search grounding could not verify tariff pages. A direct API call did.
- Models invent plausible detail (alternative 10-digit codes, "hand-made vs
  mass-produced" subcodes). Constrain what they may state and make them cite.
- Check which file you are actually running. The agent graph name in
  `adk web` showed we were still on the blank template.

## Known limits

- UK only is verified. EU relies on search and usually comes back unverified.
- Preferential rates by origin are not computed (the API has the data; a later
  upgrade could read it).
- Descriptions of subcodes are shortened; very long lists are capped at 30.

## Cost and cleanup

- Everything runs locally; only model calls (and any search grounding) are
  billed to the Google Cloud project. Nothing is deployed, so nothing bills
  while idle. The UK tariff API is free.
- Do not click "Upgrade" on the free trial; when credit or the 90 days run
  out, resources stop instead of billing.
- Remove local credentials when done:  gcloud auth application-default revoke
- Optional: delete the Google Cloud project to remove everything.

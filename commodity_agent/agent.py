"""Commodity-code assistant: a small ADK agent for learning Vertex AI agents.

Layers:
  1. Intake behaviour  - the root agent's instruction makes it ask clarifying
                         questions before proposing a code.
  2. Function tools    - lookup_uk_tariff() checks the code against the real
                         UK Trade Tariff API; validate_and_save_code() checks
                         the code's shape, caps confidence, and stores the
                         result in session state.
  3. Search grounding  - a sub-agent with Google Search (used for EU/TARIC),
                         wrapped as a tool so it can sit next to the function
                         tools.

Live test of the UK lookup (from the project folder, venv active):
    python commodity_agent/agent.py 9503003990
"""

import json
import re
import sys
import urllib.error
import urllib.request

from google.adk.agents import Agent
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext

MODEL = "gemini-3.6-flash"

# Note: no temperature/top_p/top_k set on purpose - Google has deprecated
# those parameters for the newest Gemini models.

TARIFF_API = "https://www.trade-tariff.service.gov.uk/api/v2"
TARIFF_PAGE = "https://www.trade-tariff.service.gov.uk/commodities/"
THIRD_COUNTRY_DUTY = "103"  # measure type id for "Third country duty"


# --- Layer 2a: real UK tariff lookup (free public API, no Vertex cost) -------
def _get_json(path: str) -> dict:
    req = urllib.request.Request(
        TARIFF_API + path,
        headers={
            "Accept": "application/json",
            "User-Agent": "commodity-agent-learning/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _suggest_subcodes(digits: str) -> list:
    """Best-effort list of declarable codes near a code that was not found."""
    try:
        payload = _get_json(f"/headings/{digits[:4]}")
    except Exception:
        return []
    out = []
    for item in payload.get("included", []):
        if item.get("type") != "commodity":
            continue
        attrs = item.get("attributes", {})
        code = str(attrs.get("goods_nomenclature_item_id", ""))
        if attrs.get("declarable") and code.startswith(digits[:6]):
            out.append(
                {
                    "code": code,
                    "description": _clean(
                        attrs.get("description_plain")
                        or attrs.get("description", "")
                    ),
                }
            )
    # Keep ALL nearby codes (up to 30): the generic "Other" subcodes usually
    # sit after the niche "for use in the manufacture of..." ones.
    return out[:30]


def _clean(text: str, limit: int = 110) -> str:
    """Strips HTML tags/non-breaking spaces and shortens a description."""
    text = re.sub(r"<[^>]+>", " ", text).replace(" ", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def lookup_uk_tariff(commodity_code: str) -> dict:
    """Checks a 10-digit commodity code against the official UK Trade Tariff.

    Use this for UK imports. It says whether the exact code exists and is
    declarable, and returns the standard (third-country) duty rate.

    Args:
        commodity_code: The full 10-digit UK commodity code, digits only or
            with spaces/dots (e.g. "9503003990").

    Returns:
        A dict. "exists" is True, False or "unknown". When exists is True:
        "declarable", "description", "standard_duty" and "source_url". When
        exists is False: "suggestions", a list of nearby declarable codes.
        When status is "error", treat the code as unverified.
    """
    digits = re.sub(r"[\s.]", "", commodity_code)
    if not digits.isdigit() or len(digits) != 10:
        return {
            "status": "error",
            "exists": "unknown",
            "message": "Give the full 10-digit UK commodity code.",
        }

    try:
        payload = _get_json(f"/commodities/{digits}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {
                "status": "ok",
                "exists": False,
                "code": digits,
                "suggestions": _suggest_subcodes(digits),
            }
        return {
            "status": "error",
            "exists": "unknown",
            "message": f"Tariff API returned HTTP {e.code}.",
        }
    except Exception as e:  # network down, timeout, bad JSON...
        return {
            "status": "error",
            "exists": "unknown",
            "message": f"Lookup failed: {e}",
        }

    attrs = payload.get("data", {}).get("attributes", {})
    included = payload.get("included", [])
    by_key = {(i.get("type"), i.get("id")): i for i in included}

    measures_checked = 0
    standard_duty = None
    for item in included:
        if item.get("type") != "measure":
            continue
        measures_checked += 1
        rel = item.get("relationships", {})
        measure_type = (rel.get("measure_type") or {}).get("data") or {}
        if measure_type.get("id") != THIRD_COUNTRY_DUTY:
            continue
        expr_ref = (rel.get("duty_expression") or {}).get("data") or {}
        expr = by_key.get(("duty_expression", expr_ref.get("id")))
        if expr:
            standard_duty = expr.get("attributes", {}).get("base")
            if standard_duty:
                break

    return {
        "status": "ok",
        "exists": True,
        "code": digits,
        "declarable": bool(attrs.get("declarable")),
        "description": attrs.get("description_plain")
        or attrs.get("description", ""),
        "standard_duty": standard_duty or "UNVERIFIED (not found in response)",
        "source_url": TARIFF_PAGE + digits,
        "measures_checked": measures_checked,
    }


# --- Layer 3: search-grounded sub-agent (EU / fallback) ---------------------
duty_search_agent = Agent(
    model=MODEL,
    name="duty_search",
    description=(
        "Checks an EU (TARIC) commodity code and its standard import duty "
        "using Google Search. Use for EU destinations only."
    ),
    instruction=(
        "You are a research helper for customs classification. You are "
        "given a candidate commodity code, a destination (UK or EU) and "
        "possibly an origin country. Use Google Search to check it against "
        "OFFICIAL sources only (UK: trade-tariff.service.gov.uk or gov.uk; "
        "EU: TARIC / taxation-customs.ec.europa.eu). Reply in this exact "
        "format, under 100 words:\n"
        "CODE_CHECK: FOUND (the exact full code appears on an official "
        "page) | NOT_FOUND (it does not exist, or only more specific "
        "subcodes exist - list the closest subcodes you saw) | UNVERIFIED "
        "(no official page found).\n"
        "STANDARD_DUTY: the standard third-country rate, or UNVERIFIED.\n"
        "PREFERENTIAL: one line on whether a lower rate may apply for the "
        "origin country, or 'origin not given'.\n"
        "SOURCE_URL: the full official URL you relied on, or NONE.\n"
        "Never guess a rate or a code from memory. If a third-party "
        "website is the only source, set the status to UNVERIFIED."
    ),
    tools=[google_search],
)


# --- Layer 2b: custom function tool with session state -----------------------
def validate_and_save_code(
    commodity_code: str,
    confidence: str,
    reasoning: str,
    verified_on_official_site: bool,
    plausible_alternative_heading: bool,
    tool_context: ToolContext,
) -> dict:
    """Validates the shape of a proposed commodity code and saves it.

    Call this AFTER the tariff lookup, once you have settled on a code.

    Args:
        commodity_code: The proposed code, digits only or with spaces/dots
            (6, 8 or 10 digits, e.g. "6109100010").
        confidence: One of "high", "medium" or "low".
        reasoning: One or two sentences on why this code fits.
        verified_on_official_site: True only if lookup_uk_tariff returned
            exists=True and declarable=True for this exact code, or
            duty_search returned CODE_CHECK: FOUND. Otherwise False.
        plausible_alternative_heading: True if a different 4-digit heading
            could reasonably apply (e.g. a composite product that is
            arguably a case or a battery). Decide this before calling.

    Returns:
        A dict with "status" ("ok" or "error") and details. The returned
        "confidence" is the one to report: it is capped at "medium" when
        the code was not verified on an official site, or when a different
        heading is also plausible.
    """
    digits = re.sub(r"[\s.]", "", commodity_code)

    if not digits.isdigit():
        return {"status": "error", "message": "Code must contain digits only."}
    if len(digits) not in (6, 8, 10):
        return {
            "status": "error",
            "message": f"Code has {len(digits)} digits; expected 6, 8 or 10.",
        }
    chapter = int(digits[:2])
    if not 1 <= chapter <= 97 or chapter == 77:
        return {
            "status": "error",
            "message": f"Chapter {digits[:2]} does not exist in the tariff.",
        }
    if confidence.lower() not in ("high", "medium", "low"):
        return {
            "status": "error",
            "message": 'confidence must be "high", "medium" or "low".',
        }

    # Hard guard in code (not just in the prompt): no "high" confidence for
    # a code that was not found on an official tariff page.
    confidence = confidence.lower()
    note = ""
    if confidence == "high" and not verified_on_official_site:
        confidence = "medium"
        note = "Confidence capped at medium: code not verified on an official site."
    elif confidence == "high" and plausible_alternative_heading:
        confidence = "medium"
        note = "Confidence capped at medium: a different heading is also plausible."

    # Save into session state so later turns (and the UI state tab) can see it.
    history = tool_context.state.get("proposed_codes", [])
    history.append(
        {
            "code": digits,
            "confidence": confidence,
            "verified": bool(verified_on_official_site),
            "alt_heading_plausible": bool(plausible_alternative_heading),
            "reasoning": reasoning,
        }
    )
    tool_context.state["proposed_codes"] = history

    return {
        "status": "ok",
        "code": digits,
        "chapter": digits[:2],
        "confidence": confidence,
        "verified": bool(verified_on_official_site),
        "note": note,
        "saved_count": len(history),
    }


# --- Layer 1: root agent with intake behaviour and guardrails ----------------
root_agent = Agent(
    model=MODEL,
    name="commodity_code_assistant",
    description="Helps small sellers find a likely import commodity code.",
    instruction="""You help small UK and EU online sellers find the likely
commodity (tariff) code for a product they import.

Scope: you ONLY classify products into commodity codes. You do NOT give
general importing guides (suppliers, shipping, incoterms, labelling, business
advice). If asked for those, say it is out of scope and steer back to
classification.

How you work:
1. The seller describes a product. Your FIRST reply must contain ONLY 2-3
   numbered clarifying questions, under 60 words in total, with no
   introduction, no background, no guide and no code. Ask about what decides
   classification: material or composition (e.g. % cotton), knitted vs woven
   or how it is made, intended use, finished item vs part, sold as a set.
   Always include one question asking the destination (UK or EU) AND the
   country the goods come from, unless already given. Ask only what is
   still unknown. Never skip this step, even for common products.
2. Once you have enough detail, choose the most likely code. For a UK
   destination propose a full 10-digit code.
3. Check the code against the official tariff:
   - UK destination: call lookup_uk_tariff with the 10-digit code. If
     exists is false, choose the best of its suggestions (or ask one more
     question if you cannot tell which fits) and look up again. If status
     is "error", treat the code as unverified.
   - EU destination: call duty_search with the code and origin country and
     read its CODE_CHECK line (FOUND / NOT_FOUND / UNVERIFIED).
   Make at most two lookups in total.
4. Call validate_and_save_code with the code, a confidence level
   (high/medium/low), a short reasoning, and verified_on_official_site set
   to true only if lookup_uk_tariff returned exists=true and declarable=true
   for that exact code, or duty_search returned CODE_CHECK: FOUND. Also set
   plausible_alternative_heading to true if a different 4-digit heading
   could reasonably apply (the one you would write under "Could change
   if"). A code that exists is not necessarily the right classification.
5. Reply in this format: plain text, no headings, each field on its own line
   with a blank line between fields:
   Code: <code> (Confidence: <the confidence returned by
   validate_and_save_code>)
   Why: two sentences.
   Duty: the standard rate, and one line telling the seller to check the
   source page for any preferential rate for the origin country (do not say
   that one exists).
   Source: the source_url or SOURCE_URL from the lookup, or "none found -
   unverified".
   Could change if: a different 4-digit heading or 6-digit code that differs
   from your code's first 6 digits and what detail would lead to it; if
   there is none, write "nothing obvious".

Rules:
- Never state a duty rate or a source you did not get from a lookup. If the
  standard duty is UNVERIFIED or there is no URL, say the rate is unverified.
- Never state a preferential rate as a number, and never say one exists;
  only point the seller to the source page.
- If the product is a composite or could fit two headings, say so in "Why".
- If the code is not verified, say "The first 6 digits (international
  standard) are the reliable part; check the rest at the official tariff."
- Never invent a 10-digit alternative code.
- Report the confidence returned by validate_and_save_code, not your own.
- If validate_and_save_code returns an error, fix the code and call it again.
- Always end with a short reminder: this is a suggestion, not an official
  classification. For anything binding, confirm with HMRC's trade tariff
  tools or request an advance ruling.
- Keep every reply under 170 words. No legal or tax advice beyond this.
- You must do a tariff lookup and call validate_and_save_code before giving
  any final answer. A final answer without both is wrong.
""",
    tools=[
        lookup_uk_tariff,
        validate_and_save_code,
        AgentTool(agent=duty_search_agent),
    ],
)


if __name__ == "__main__":
    # Quick live test of the UK lookup:  python commodity_agent/agent.py 9503003990
    code = sys.argv[1] if len(sys.argv) > 1 else "9503003990"
    print(json.dumps(lookup_uk_tariff(code), indent=2))

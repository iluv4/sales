# Sales Agent

A sales development agent you run from the command line. It's built on the Claude API. Tell it about your product and your ideal customer once. After that it can:

- **research** prospects on the web: what the company does, its size, recent news, hiring, and the contact's role
- **qualify** leads against your ideal customer profile, giving each a 0–100 score and the evidence behind it
- **draft outreach** (email, LinkedIn, call scripts, follow-ups) in your voice, saved for you to review
- **track the pipeline** in a local SQLite CRM: leads, stages, an activity log and drafts

The agent **never sends anything**. Drafts wait in the CRM until you review and send them yourself.

## Setup

Requires Python 3.11+.

```bash
pip install -e .
```

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

(Signing in with `ant auth login` works too.)

```bash
sales-agent init
```

`init` writes `config.toml`. Fill in the `[company]`, `[icp]` and `[outreach]` sections. Everything there goes into the agent's instructions, so be concrete: real pain points, real disqualifiers, and only the proof points the agent is allowed to cite. `config.toml` and the database are git-ignored, so your data stays local.

## Use it

Interactive session:

```bash
sales-agent chat
```

```
you> Find 5 mid-size logistics companies in Germany that fit our ICP and add them as leads
you> Research and qualify lead 3
you> Draft a first email to the strongest two
you> I had a call with Jo at Acme, they want a proposal next week. Log it and move them along.
you> What should I focus on today?
```

One-shot requests:

```bash
sales-agent ask "research and qualify Acme Corp (acme.com)"
```

Commands that don't call the API:

```bash
sales-agent leads                   # list leads, best scores first
sales-agent leads fintech --stage qualified
sales-agent show 3                  # one lead with activity log and drafts
sales-agent drafts                  # drafts awaiting review
sales-agent drafts --mark 7 sent    # after you send one yourself
sales-agent pipeline                # counts by stage, top leads, stale leads
sales-agent import leads.csv        # needs a 'company' column; optional:
                                    # contact_name,title,email,website,linkedin,
                                    # industry,company_size,location,source,notes
```

Pipeline stages: `new → researching → qualified → contacted → meeting → proposal → won / lost`, plus `disqualified`.

## How it works

| File | Role |
|---|---|
| `src/sales_agent/agent.py` | Runs the conversation with the Anthropic SDK tool runner (`client.beta.messages.tool_runner`). It keeps the history, rolls back a turn that fails, and handles refusals and output limits. |
| `src/sales_agent/tools.py` | The CRM tools Claude can call: `add_lead`, `update_lead`, `get_lead`, `search_leads`, `qualify_lead`, `log_activity`, `save_outreach_draft`, `pipeline_summary`. Also turns on Anthropic's hosted `web_search` / `web_fetch` tools. |
| `src/sales_agent/prompts.py` | Builds the system prompt from your config. |
| `src/sales_agent/store.py` | The SQLite CRM. |
| `src/sales_agent/config.py` | Loads `config.toml` and validates it. |

API settings, defaults first (change them under `[agent]` in `config.toml`):

- **Model:** `claude-opus-5-5`.
- **Effort:** `high`. Adaptive thinking is on.
- **Prompt caching:** the system prompt, tools and history are cached, so long sessions stay cheap.
- **Refusal fallback:** server-side refusal fallback (`fallbacks: "default"`) is on for models that support it. If the main model declines a request, the API retries it on a fallback model.
- **Reply language:** `reply_language = "Korean"` (default). The agent always answers you in this language; outreach drafts follow the prospect's language.
- **Web research:** set `web_research = false` to keep the agent off the web. `max_web_searches` caps searches per request.

## Development

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

The tests use a fake API client, so they need no network access and no API key.

# NVIDIA Build: Türkçe finans / kripto içerik üretimi

`POST /api/content/generate` generates a Turkish script, three suggested titles,
a description and up to six hashtags. The isolated backend studio includes a
separate draft form. Generation never creates a TikTok job, changes the caption,
selects visibility, grants consent or publishes a video. Review factual accuracy,
language, suitability and timing before using a draft; there is no live market-data feed.

## Official sources and model

Implementation follows NVIDIA's official hosted-NIM examples and API documentation:

- [NVIDIA API reference](https://docs.api.nvidia.com/nim/reference/llm-apis)
- [Llama 3.3 API/model catalog](https://build.nvidia.com/meta/llama-3_3-70b-instruct)
- [Official hosted model example, pinned revision](https://github.com/NVIDIA/GenerativeAIExamples/blob/203b0de48d1142124842d56d4b40f3733888f5ee/community/chat-and-rag-glean/README.md): explicitly documents `meta/llama-3.3-70b-instruct` with `NVIDIA_API_KEY`.
- [Official OpenAI-compatible request example, pinned revision](https://github.com/NVIDIA/GenerativeAIExamples/blob/203b0de48d1142124842d56d4b40f3733888f5ee/community/llm-prompt-design-helper/api_request_backends/openai_client.py): documents `https://integrate.api.nvidia.com/v1`, chat messages, model, max_tokens and sampling parameters.

The documentation website was blocked by the cloud egress proxy during development;
the official NVIDIA GitHub sources above were actually retrieved and inspected.
The default catalog model is `meta/llama-3.3-70b-instruct`. Availability for this
account and quality of real Turkish output remain unverified without a real key.
An administrator can select another compatible text model using `NVIDIA_MODEL`.
The inference endpoint is fixed to HTTPS NVIDIA Build; callers cannot override its host.

## Credentials: only in secret environment settings

Development and mock tests do **not** require a NVIDIA key or real TikTok credentials.

Account owner actions:

1. Sign into [NVIDIA Build](https://build.nvidia.com/), open the model and obtain an
   API key with hosted inference access. Confirm the account's current credits,
   price/rate limits and model access; these are provider controlled.
2. For a real cloud test, open the Codex environment's **Secrets / personal secrets**
   settings, create `NVIDIA_API_KEY`, and bind it to the environment variable with
   the same name. Its allowed outbound host is `integrate.api.nvidia.com`.
   The onboarding binding is optional (`user_provided`), not a mandatory blank field.
   Review/save/publish the pending environment network settings and restart the
   environment if required. Do not paste the value into chat, source, command-line
   arguments, a frontend input, a `.env.example`, PR text or GitHub.
3. For deployed use, add `NVIDIA_API_KEY` to **Environment** on the separate Render
   Docker backend service. Keep the existing Render static service unchanged.
   A Codex secret is not automatically transferred to Render. Keep
   `ALLOW_PUBLIC_POSTS=false`. Deployment of the TikTok backend still needs its
   existing OAuth and token-encryption configuration, documented in README.md.

`NVIDIA_MODEL` is optional, non-secret, with the default above. No additional SDK,
CUDA/GPU, NVIDIA callback URI, client ID or OAuth secret is needed. NVIDIA generation
is disabled when the key is absent; TikTok health/readiness and posting continue.
The public generator endpoint uses the existing authenticated TikTok session and
Origin/CSRF protections. This prevents anonymous visitors spending the server's key.

## API and bounded usage

Send JSON with `topic` (1–300 characters), `category` (`finance` or `crypto`),
`duration_seconds` (30/60/90) and optional `context` (up to 1000 characters).
An 8 KiB body limit applies before parsing. Include the existing session cookie,
`Origin`, `X-CSRF-Token` and a unique 16–128 printable ASCII `Idempotency-Key`.
There is no caller-provided API key, model, endpoint or token-budget parameter.

Server limits are 5 attempts/minute/account, 20 attempts/rolling 24 hours/account,
100 attempts/rolling 24 hours/server, and two concurrent provider requests per
backend process. Each request caps generated output at 1600 tokens, uses a 60-second
read timeout and a 10-second connect timeout. Input is bounded too. These limits
bound requests and output; they do not guarantee a monetary cost or override
NVIDIA account limits. Run the existing single-worker deployment with one shared
persistent database; independent replicas/databases would have independent quotas.

An atomic SQLite reservation counts **attempts**, including upstream failures.
Quotas survive restarts. Successful retries with the same key and same input return
the cached result without another billable request. A changed input with that key,
or a previous pending/failed attempt, returns 409. There are no automatic provider
retries, including on 429, timeout, or uncertain transport failure. To intentionally
make a new attempt after reviewing an error, change the form input (or supply a
new key as an API caller); this consumes another attempt and may incur cost.

Responses must be complete, valid, bounded JSON; truncated/malformed results return
502. Invalid provider credentials/access return a sanitized 503, transport errors
503/504, and provider quota errors 429 with a bounded Retry-After header. Raw provider
bodies and credentials are never returned. A missing key returns 503 before reserving
budget. A configured key alone does not prove provider availability.

Generated results are encrypted in the existing private SQLite database with
`TOKEN_ENCRYPTION_KEY`. Only hashed account IDs, request IDs, request fingerprints
and timestamps are retained as quota metadata. Requests/prompts are not persisted.
Cache rows expire after 24 hours and are purged on the next generation reservation;
disconnecting clears cached content immediately and keeps usage metadata until expiry
so reconnecting cannot reset the server budget. Database backups remain subject to
the operator's retention policy. Text sent for generation goes to NVIDIA; don't put
account credentials or personal financial data into a topic/context.

## Operator CLI and real test

From `pararadar-web/backend`, with `NVIDIA_API_KEY` securely injected:

```sh
python scripts/generate_content.py 'Kripto varlıklarda risk yönetimi' --category crypto --duration 60
RUN_LIVE_NVIDIA=1 python -m pytest tests/test_nvidia.py::test_live_nvidia -q
```

The CLI works without TikTok OAuth configuration, makes one bounded request per
invocation, prints JSON and exits nonzero on provider errors. It is a trusted local
operator tool; the web endpoint's per-account SQLite quotas do not apply to the CLI
or live contract test. Both may consume provider credits. They never upload a video.
The live test skips unless the key and explicit `RUN_LIVE_NVIDIA=1` opt-in are present. Do not repeatedly run it to poll readiness.

## Validation (2026-10-08)

Local Python 3.12 run: **116 passed, 2 skipped**, total coverage **96.17%**;
NVIDIA module coverage **99%**. Four real Chromium tests run against a local HTTPS
backend with explicitly mocked upstreams; the new test verifies content rendering,
three titles and unchanged TikTok consent/caption/job count. Existing TikTok tests
remain included. Tests cover auth/CSRF, malformed/bounded input/output, safe upstream
errors, timeouts, no redirects/retries, idempotency, encrypted restart persistence,
atomic concurrent quota reservations, all rolling quota limits and disconnect cleanup.
Docker build and container smoke checks also passed: readiness/UI, private database,
non-root UID 10001 and encrypted persistence after restart. Static pages, styles and
the TikTok verification file are byte-identical to the preceding TikTok branch.
The two skipped tests are real NVIDIA and real TikTok; no real key was available.
Mock success is not evidence of live integration. A FastAPI/Starlette test-client
upstream deprecation warning is non-failing.

The NVIDIA PR is stacked on the existing unmerged TikTok backend PR #1. Merge that
backend first, then retarget/review this PR against main. Neither PR deploys or merges
itself. Existing static-site assets and TikTok verification files are not modified.

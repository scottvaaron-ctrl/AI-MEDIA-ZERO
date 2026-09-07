# Introducing a funded V1 without redesigning the system

V0 is built so that money is a configuration change plus new provider classes, not a rewrite.

## 1. Raise the budget (owner)

```
MONTHLY_BUDGET_USD=25.00
ALLOW_PAID_PROVIDERS=true
```

Nothing else changes yet: no paid provider is registered, and every call still passes
`BudgetManager.authorize()`. The ledger now has headroom.

## 2. Add a paid provider

Copy the template in `src/aimz/providers/paid_examples.py`:

```python
class ElevenLabsTTSProvider(TTSProvider):
    name = "ElevenLabsTTSProvider"
    is_paid = True
    def estimate_cost(self, purpose, **kw): return kw.get("chars", 0) / 1000 * 0.30
    def synthesize(self, ctx, text, out_path, purpose="narration"):
        with self.authorized(ctx, purpose, chars=len(text)) as rec:
            ...call the API...
            rec.actual_cost = <billed amount>
```

Rules the base class enforces for you:

- kill switch checked first (`is_paid=True`);
- `BudgetManager.authorize()` before any I/O; `BudgetDenied` if the projection exceeds the cap;
- `provider_usage` + `ledger` rows with estimated and actual cost;
- the agent-run span accumulates the cost.

Register it in `providers/registry.py` under a new `.env` switch (e.g. `TTS_PROVIDER=elevenlabs`)
guarded by `env.allow_paid_providers and env.monthly_budget_usd > 0`.

Candidates, in order of expected impact per dollar:

| Need | Provider class | Why |
|---|---|---|
| Better scripts | `OpenAICompatibleLLMProvider` (any hosted model) | quality of hooks and payoff is the main lever |
| Better voice | paid TTS | retention on short-form is voice-sensitive |
| Stock footage / b-roll | paid `AssetProvider` | motion beats stills |
| Generated images | hosted image API | when local GPU is insufficient |
| Cloud rendering | remote `VideoProvider` | throughput, not quality |

Keep `OllamaProvider`, `PiperTTSProvider` and `FFmpegRenderer` as fallbacks: if a paid call is
denied or fails, the agent can fall back to the free provider (add the fallback in the registry,
not in the agents).

## 3. Revenue reinvestment (still owner-gated)

The ledger already tracks `revenue`, `owner_injections`, `spend`, `reinvestment`, `profit`.
To allow the AI to reinvest, add an owner rule in `.env`:

```
REINVEST_MAX_PERCENT=50
```

and extend `BudgetManager.snapshot()` so `budget_usd = MONTHLY_BUDGET_USD + min(revenue * pct, cap)`.
No agent code changes; the cap simply grows with earned revenue. Leave it unset in V0.

## 4. Move to Postgres / Supabase

The schema already uses TEXT ids, ISO timestamps, JSON-as-TEXT and `?` placeholders.

1. Add a `PostgresDatabase` implementing the same `execute/query/one/scalar/insert/upsert/update`
   surface as `db/connection.py`, translating `?` → `%s` and `INSERT ... ON CONFLICT` as-is.
2. Run the same numbered migrations (the SQL is portable; `datetime('now', ...)` expressions in
   three queries need a `NOW() - INTERVAL` variant).
3. Point `AIMZ_DB_URL` at the new backend. Nothing in agents or providers touches SQLite directly.

## 5. Hosting

The dashboard is a plain FastAPI app; the cycle is a CLI command. A $5 VPS or a home server
with Task Scheduler / cron is enough. Ollama and Piper run anywhere with a GPU or a patient CPU.

## 6. Public, automated posting

- YouTube: complete the API compliance audit, then set `YOUTUBE_MODE=public`.
- TikTok: complete the app audit, implement `TikTokDirectPostPublisher` behind the existing
  `Publisher` interface (the package publisher already prepares every field it needs).

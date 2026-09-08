"""Service container: builds every provider from settings and hands agents a single object.

Paid providers are never constructed unless the owner sets ``ALLOW_PAID_PROVIDERS=true`` **and**
``MONTHLY_BUDGET_USD > 0``; even then each call is still authorized against the budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from aimz.core.budget import BudgetManager
from aimz.core.killswitch import KillSwitch
from aimz.core.runs import AgentSpan, RunTracker
from aimz.db import Database, connect
from aimz.logging_setup import setup_logging
from aimz.providers.analytics.store import SQLiteAnalyticsProvider
from aimz.providers.analytics.tiktok import TikTokAnalyticsProvider
from aimz.providers.analytics.youtube import YouTubeAnalyticsProvider
from aimz.providers.assets.owner_library import OwnerLibraryAssetProvider
from aimz.providers.assets.wikimedia import WikimediaAssetProvider
from aimz.providers.base import (
    AnalyticsProvider,
    AssetProvider,
    LLMProvider,
    Provider,
    ProviderContext,
    Publisher,
    ResearchProvider,
    TTSProvider,
)
from aimz.providers.image.cards import PillowCardProvider
from aimz.providers.llm.fixture import FixtureLLMProvider
from aimz.providers.llm.ollama import OllamaProvider
from aimz.providers.publishers.tiktok import TikTokPackagePublisher
from aimz.providers.publishers.tiktok_direct import TikTokClient, TikTokDirectPostPublisher
from aimz.providers.publishers.youtube import YouTubePublisher
from aimz.providers.research.rss import RSSResearchProvider
from aimz.providers.tts.piper import PiperTTSProvider
from aimz.providers.tts.silent import SilentTTSProvider
from aimz.providers.video.ffmpeg_renderer import FFmpegRenderer
from aimz.settings import AppConfig, EnvSettings, load_app_config, load_env_settings, load_pronunciations

log = logging.getLogger("aimz.registry")


@dataclass
class Services:
    env: EnvSettings
    config: AppConfig
    db: Database
    budget: BudgetManager
    killswitch: KillSwitch
    tracker: RunTracker
    llm: LLMProvider
    tts: TTSProvider
    cards: PillowCardProvider
    renderer: FFmpegRenderer
    assets: list[AssetProvider]
    research: dict[str, ResearchProvider]
    publishers: dict[str, Publisher]
    analytics_store: SQLiteAnalyticsProvider
    remote_analytics: dict[str, AnalyticsProvider]
    disabled_paid: list[str] = field(default_factory=list)

    def ctx(
        self, run_id: str | None = None, content_id: str | None = None, span: AgentSpan | None = None
    ) -> ProviderContext:
        return ProviderContext(self.db, self.budget, self.killswitch, run_id, content_id, span)

    def all_providers(self) -> list[Provider]:
        out: list[Provider] = [
            self.llm,
            self.tts,
            self.cards,
            self.renderer,
            *self.assets,
            *self.research.values(),
            *self.publishers.values(),
            self.analytics_store,
            *self.remote_analytics.values(),
        ]
        seen: set[int] = set()
        uniq: list[Provider] = []
        for p in out:
            if id(p) not in seen:
                seen.add(id(p))
                uniq.append(p)
        return uniq

    @property
    def project_root(self) -> Path:
        return self.env.project_root

    def close(self) -> None:
        self.db.close()


def build_services(
    project_root: Path | None = None, *, quiet_logs: bool = False, env: EnvSettings | None = None
) -> Services:
    env = env or load_env_settings(project_root)
    env.data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(env.data_dir, env.log_level, quiet=quiet_logs)
    config = load_app_config(env.config_dir)
    db = connect(env.db_path)
    budget = BudgetManager(db, env.monthly_budget_usd)
    killswitch = KillSwitch(db, env.data_dir)
    tracker = RunTracker(db)

    # ---- LLM -------------------------------------------------------------------------
    llm: LLMProvider
    if env.llm_provider == "fixture":
        llm = FixtureLLMProvider()
    else:
        llm = OllamaProvider(env.ollama_host, env.ollama_model, env.ollama_timeout_s, env.ollama_num_ctx)

    # ---- TTS -------------------------------------------------------------------------
    tts: TTSProvider
    if env.tts_provider == "silent":
        tts = SilentTTSProvider()
    else:
        tts = PiperTTSProvider(
            env.piper_voices_dir,
            env.piper_voice,
            env.piper_auto_download,
            env.piper_length_scale,
            load_pronunciations(env.config_dir),
            user_agent=env.user_agent,
        )

    # ---- visuals ---------------------------------------------------------------------
    res = config.get("content.short_form.resolution", [1080, 1920]) or [1080, 1920]
    cards = PillowCardProvider(env.font_file, int(res[0]), int(res[1]))
    renderer = FFmpegRenderer(env.ffmpeg_bin, env.ffprobe_bin)

    assets: list[AssetProvider] = []
    owner_dir = Path(config.get("assets.owner_library_dir", "./assets/owner"))
    owner_dir = owner_dir if owner_dir.is_absolute() else env.project_root / owner_dir
    assets.append(OwnerLibraryAssetProvider(owner_dir))
    if config.get("assets.wikimedia.enabled", True):
        assets.append(WikimediaAssetProvider(env.user_agent, config.get("assets.wikimedia.allowed_licenses")))

    # ---- research --------------------------------------------------------------------
    rss = RSSResearchProvider(env.user_agent)
    research: dict[str, ResearchProvider] = {k: rss for k in rss.kinds}

    # ---- publishers ------------------------------------------------------------------
    publishers: dict[str, Publisher] = {
        "youtube": YouTubePublisher(
            env.youtube_mode,
            env.youtube_default_privacy,
            env.youtube_enabled,
            env.youtube_client_secret_file,
            env.youtube_token_file,
            str(config.get("publishing.youtube.category_id", "27")),
            bool(config.get("publishing.youtube.made_for_kids", False)),
            bool(config.get("publishing.youtube.contains_synthetic_media", True)),
        ),
        "tiktok": TikTokPackagePublisher(bool(config.get("publishing.tiktok.recommend_ai_label", True))),
    }
    tiktok_client: TikTokClient | None = None
    if env.tiktok_mode == "direct" or env.tiktok_analytics_enabled:
        tiktok_client = TikTokClient(
            env.tiktok_client_key, env.tiktok_client_secret, env.tiktok_redirect_uri, env.tiktok_token_file
        )
    if env.tiktok_mode == "direct" and tiktok_client is not None:
        publishers["tiktok"] = TikTokDirectPostPublisher(
            tiktok_client,
            env.tiktok_privacy_level,
            bool(config.get("publishing.tiktok.recommend_ai_label", True)),
        )

    # ---- analytics -------------------------------------------------------------------
    store = SQLiteAnalyticsProvider(db)
    remote: dict[str, AnalyticsProvider] = {}
    if env.youtube_analytics_enabled:
        remote["youtube"] = YouTubeAnalyticsProvider(True, env.youtube_token_file)
    if env.tiktok_analytics_enabled and tiktok_client is not None:
        remote["tiktok"] = TikTokAnalyticsProvider(tiktok_client, db)

    disabled_paid = ["PaidTTSProviderExample", "PaidLLMProviderExample"]
    if env.allow_paid_providers and env.monthly_budget_usd > 0:
        log.warning(
            "ALLOW_PAID_PROVIDERS is on with a non-zero budget; no paid providers are wired in V0 (templates only)"
        )

    return Services(
        env=env,
        config=config,
        db=db,
        budget=budget,
        killswitch=killswitch,
        tracker=tracker,
        llm=llm,
        tts=tts,
        cards=cards,
        renderer=renderer,
        assets=assets,
        research=research,
        publishers=publishers,
        analytics_store=store,
        remote_analytics=remote,
        disabled_paid=disabled_paid,
    )

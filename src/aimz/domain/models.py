"""Pydantic models.

Two groups:

* **LLM output schemas** (``*Draft``, ``*Result``): used both as the JSON-schema handed to
  Ollama's structured-output mode and as the validator for what comes back.
* **Internal records** (``Scene``, ``Timeline``, ``AssetRecord`` ...): passed between the
  producer, renderer, and publishers.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

Platform = Literal["tiktok", "youtube_shorts", "both", "youtube_long"]
Difficulty = Literal["low", "medium", "high"]
Risk = Literal["low", "medium", "high"]
VisualType = Literal["image", "text_card", "chart", "map", "timeline", "quote_card", "stat_card"]


# --------------------------------------------------------------------------------------
# Research
# --------------------------------------------------------------------------------------
class FetchedItem(BaseModel):
    url: str
    title: str
    summary: str = ""
    published_at: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SourceItemView(BaseModel):
    """What ideation sees for each lead."""

    id: str
    source_name: str
    url: str
    title: str
    summary: str = ""
    category: str | None = None
    published_at: str | None = None
    credibility: float = 0.5
    freshness_score: float = 0.5


# --------------------------------------------------------------------------------------
# Ideation
# --------------------------------------------------------------------------------------
class IdeaScores(BaseModel):
    hook_strength: int = Field(ge=0, le=10)
    curiosity: int = Field(ge=0, le=10)
    audience_relevance: int = Field(ge=0, le=10)
    trend_velocity: int = Field(ge=0, le=10)
    competition_gap: int = Field(ge=0, le=10)
    source_quality: int = Field(ge=0, le=10)
    originality: int = Field(ge=0, le=10)
    evergreen_potential: int = Field(ge=0, le=10)
    repeatability: int = Field(ge=0, le=10)
    monetization_potential: int = Field(ge=0, le=10)
    production_feasibility: int = Field(ge=0, le=10)
    zero_budget_feasibility: int = Field(ge=0, le=10)


class IdeaDraft(BaseModel):
    title: str = Field(min_length=4, max_length=120)
    premise: str = Field(min_length=10)
    hook: str = Field(min_length=5, description="The literal first sentence the viewer hears.")
    hook_type: str
    content_family: str
    target_platform: Platform = "both"
    suggested_runtime_s: int = Field(ge=15, le=900)
    source_refs: list[str] = Field(description="Ids of the source items this idea is built on.")
    production_difficulty: Difficulty = "low"
    originality_assessment: str = ""
    scores: IdeaScores
    rationale: str = ""


class IdeaBatch(BaseModel):
    ideas: list[IdeaDraft]


# --------------------------------------------------------------------------------------
# Editor-in-Chief
# --------------------------------------------------------------------------------------
class IdeaRejection(BaseModel):
    idea_id: str
    reason: str


class SelectionDecision(BaseModel):
    selected_idea_ids: list[str]
    rejected: list[IdeaRejection] = Field(default_factory=list)
    notes: str = ""


# --------------------------------------------------------------------------------------
# Script
# --------------------------------------------------------------------------------------
class Beat(BaseModel):
    narration: str = Field(min_length=3, description="Spoken words for this beat, one to three sentences.")
    caption: str = Field(default="", description="Short on-screen text (max ~8 words).")
    visual_type: VisualType = "text_card"
    visual_query: str = Field(default="", description="Search phrase for a free image, or the card text.")
    source_refs: list[str] = Field(default_factory=list)


class ClaimDraft(BaseModel):
    text: str
    claim_type: Literal["date", "number", "name", "event", "general"] = "general"
    beat_index: int = 0
    source_refs: list[str] = Field(default_factory=list)


class ScriptDraft(BaseModel):
    title: str = Field(min_length=4, max_length=100)
    hook_line: str = Field(min_length=5)
    beats: list[Beat] = Field(min_length=5, max_length=14)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    thumbnail_concept: str = ""
    claims: list[ClaimDraft] = Field(default_factory=list)

    MIN_WORDS: ClassVar[int] = 60

    @model_validator(mode="after")
    def _enough_words(self) -> ScriptDraft:
        # Enforced in the schema so a thin draft fails validation and the error is fed back to the model.
        n = sum(len(b.narration.split()) for b in self.beats)
        if n < self.MIN_WORDS:
            raise ValueError(
                f"beats contain only {n} spoken words; write at least {self.MIN_WORDS} words across 5-8 beats of 18-30 words each"
            )
        return self


# --------------------------------------------------------------------------------------
# Fact-check
# --------------------------------------------------------------------------------------
class ClaimVerdict(BaseModel):
    claim_index: int
    status: Literal["supported", "weak", "unverifiable", "contradicted"]
    note: str = ""
    suggested_rewrite: str = Field(
        default="", description="Softened wording if weak/unverifiable; empty to remove."
    )


class FactCheckResult(BaseModel):
    verdicts: list[ClaimVerdict]
    overall: Literal["pass", "revise", "reject"]
    notes: str = ""


# --------------------------------------------------------------------------------------
# Critic
# --------------------------------------------------------------------------------------
class CriticResult(BaseModel):
    passed: bool = Field(alias="pass")
    score: int = Field(ge=0, le=100)
    problems: list[str] = Field(default_factory=list)
    required_revisions: list[str] = Field(default_factory=list)
    optional_improvements: list[str] = Field(default_factory=list)
    hook_strength: int = Field(ge=0, le=10)
    originality: int = Field(ge=0, le=10)
    factual_support: int = Field(ge=0, le=10)
    pacing: int = Field(ge=0, le=10)
    payoff: int = Field(ge=0, le=10)
    clarity: int = Field(ge=0, le=10)
    copyright_risk: Risk = "low"
    policy_risk: Risk = "low"
    deceptive_media_risk: Risk = "low"
    feels_generic_ai: bool = False
    deserves_video: bool = True
    elevated_review_required: bool = False
    elevated_review_reasons: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# --------------------------------------------------------------------------------------
# Learning / strategy
# --------------------------------------------------------------------------------------
class ExperimentProposal(BaseModel):
    name: str
    hypothesis: str
    variable: str
    control: str
    treatment: str
    primary_kpi: str
    secondary_kpi: str = ""


class FamilyUpdate(BaseModel):
    key: str
    status: Literal["hypothesis", "testing", "winning", "losing", "retired", "new"]
    note: str = ""


class StrategyUpdate(BaseModel):
    audience_model: str
    family_updates: list[FamilyUpdate] = Field(default_factory=list)
    strong_hooks: list[str] = Field(default_factory=list)
    weak_hooks: list[str] = Field(default_factory=list)
    runtime_observations: str = ""
    source_observations: str = ""
    production_bottlenecks: str = ""
    audience_requests: list[str] = Field(default_factory=list)
    new_experiments: list[ExperimentProposal] = Field(default_factory=list)
    retire_experiment_ids: list[str] = Field(default_factory=list)
    explore_ratio_suggestion: float = Field(ge=0.0, le=1.0, default=0.7)
    confidence: float = Field(ge=0.0, le=1.0, default=0.1)
    change_summary: str
    notes: str = ""


class CommentClassification(BaseModel):
    classification: Literal[
        "question",
        "correction",
        "content_request",
        "confusion",
        "disagreement",
        "joke",
        "follow_up",
        "audience_signal",
        "spam",
    ]
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    useful: bool = False
    content_request: str = ""


class CommentBatchClassification(BaseModel):
    results: list[CommentClassification]


# --------------------------------------------------------------------------------------
# Production
# --------------------------------------------------------------------------------------
class AssetCandidate(BaseModel):
    provider: str
    title: str
    page_url: str
    file_url: str
    license: str
    license_url: str = ""
    author: str = ""
    attribution: str = ""
    width: int = 0
    height: int = 0
    mime: str = ""


class AssetRecord(BaseModel):
    id: str
    provider: str
    kind: str
    title: str
    file_path: str
    page_url: str = ""
    source_url: str = ""
    license: str = ""
    license_url: str = ""
    attribution: str = ""
    author: str = ""
    width: int = 0
    height: int = 0


class Scene(BaseModel):
    idx: int
    start_s: float = 0.0
    duration_s: float = 0.0
    narration: str
    caption: str = ""
    visual_type: VisualType = "text_card"
    asset_id: str | None = None
    asset_path: str | None = None
    crop: str = "cover"
    zoom: str = "in"  # in | out | none
    animation: str = "kenburns"
    transition: str = "cut"
    source: str = ""
    disclosure: str = ""
    notes: str = ""
    audio_path: str | None = None
    image_path: str | None = None
    caption_chunks: list[tuple[float, float, str]] = Field(default_factory=list)


class Timeline(BaseModel):
    video_id: str
    title: str
    width: int = 1080
    height: int = 1920
    fps: int = 30
    scenes: list[Scene]
    total_duration_s: float = 0.0
    disclosure_text: str = ""
    sources: list[dict[str, str]] = Field(default_factory=list)
    attributions: list[str] = Field(default_factory=list)


class RenderResult(BaseModel):
    video_path: str
    thumbnail_path: str
    captions_path: str
    duration_s: float


class PublishResult(BaseModel):
    status: Literal["packaged", "uploaded", "published", "blocked", "failed"]
    platform_video_id: str | None = None
    url: str | None = None
    package_dir: str | None = None
    privacy: str | None = None
    message: str = ""


class MetricsSnapshot(BaseModel):
    views: int | None = None
    impressions: int | None = None
    ctr: float | None = None
    retention_3s: float | None = None
    avg_watch_time_s: float | None = None
    avg_percent_viewed: float | None = None
    completion_rate: float | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    subscribers_gained: int | None = None
    followers_gained: int | None = None
    returning_viewers: int | None = None
    watch_time_minutes: float | None = None
    revenue_usd: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class FetchedComment(BaseModel):
    platform_comment_id: str
    author: str = ""
    text: str
    posted_at: str | None = None

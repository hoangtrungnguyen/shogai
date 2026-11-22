"""Structured-output schemas Gemini fills in for each pipeline stage."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "identity",
    "personality",
    "communication",
    "interests",
    "preferences",
    "values",
    "work_education",
    "relationships",
    "life_events",
    "appearance",
    "health_wellbeing",
    "relationship_with_user",
    "other",
]

Certainty = Literal["confirmed", "likely", "uncertain"]


class Observation(BaseModel):
    category: Category
    statement: str = Field(description="One atomic fact or inference about the subject, as a standalone sentence.")
    evidence: str = Field(description="What in the source supports it: a short quote, visual detail, or timestamp.")
    certainty: Certainty = Field(
        description="confirmed = directly stated or clearly shown; likely = reasonable inference; uncertain = weak signal."
    )
    when: str | None = Field(default=None, description="Date or period it refers to, if known (e.g. '2024-06', 'summer 2023').")
    sensitive: bool = Field(
        default=False,
        description="True if it concerns health, religion, sexuality, politics, finances or similar private matters.",
    )


class PersonMention(BaseModel):
    name: str = Field(description="Name, or a short description if unnamed (e.g. 'woman in red jacket').")
    relation_to_subject: str = Field(description="How they relate to the subject, if known (e.g. 'sister', 'coworker', 'unknown').")


class SourceAnalysis(BaseModel):
    title: str = Field(description="Short descriptive title for this source, at most 8 words.")
    summary: str = Field(description="2-4 sentences on what this source reveals about the subject.")
    about_subject: bool = Field(description="False if the source does not appear to concern the subject at all.")
    observations: list[Observation] = Field(description="Atomic observations about the subject.")
    people: list[PersonMention] = Field(default_factory=list, description="Other people appearing or mentioned.")
    open_questions: list[str] = Field(default_factory=list, description="Useful things to ask or verify later.")


class ImageAnalysis(SourceAnalysis):
    image_type: Literal[
        "photo_of_person", "group_photo", "screenshot_chat", "screenshot_social", "document", "object_or_place", "other"
    ]
    is_portrait: bool = Field(
        description="True only if this is a clear photo in which the subject is the main person, usable as a profile picture."
    )
    visible_text: str | None = Field(default=None, description="Relevant text visible in the image (OCR), condensed.")


class KeyMoment(BaseModel):
    timestamp: str = Field(description="MM:SS (or H:MM:SS) position in the recording.")
    description: str


class MediaAnalysis(SourceAnalysis):
    key_moments: list[KeyMoment] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list, description="Notable verbatim quotes from the subject, if speech is present.")


class PaperClaim(BaseModel):
    claim: str = Field(description="A key claim or finding, stated precisely (include effect sizes / samples when given).")
    location: str | None = Field(default=None, description="Section or page where it appears.")
    relevance: str = Field(description="Why this claim matters for understanding or relating to the subject.")


class AcademicExtraction(SourceAnalysis):
    paper_title: str
    authors: list[str]
    year: str | None = None
    venue: str | None = Field(default=None, description="Journal, conference, or 'preprint (arXiv)' etc.")
    identifier: str | None = Field(default=None, description="DOI, arXiv id or URL if present.")
    subject_relation: Literal["author", "mentioned", "topic_relevant", "unrelated"]
    subject_role: str | None = Field(default=None, description="E.g. 'first author, affiliated with X University'.")
    key_claims: list[PaperClaim] = Field(description="The 3-6 most important claims.")
    methodology: str = Field(description="Design, sample, measures and stated limitations, in 2-4 sentences.")
    credibility_flags: list[str] = Field(
        default_factory=list,
        description="Concerns such as: preprint/not peer reviewed, small or unrepresentative sample, conflicts of interest.",
    )


class ClaimCheck(BaseModel):
    claim: str
    verdict: Literal["supported", "partially_supported", "contested", "contradicted", "unverifiable"]
    explanation: str = Field(description="1-3 sentences naming the evidence (replications, reviews, reputable sources).")


class FactCheck(BaseModel):
    publication_status: str = Field(
        description="Peer-review status and venue, plus any retraction, correction or notable criticism. Say if unverifiable."
    )
    authorship_check: str | None = Field(
        default=None, description="Whether the subject is confirmed as an author, with affiliation, if relevant."
    )
    checks: list[ClaimCheck]
    applicability: str = Field(
        description="How far the findings can be applied to the subject as an individual (population vs individual)."
    )
    overall: str = Field(description="One-paragraph overall assessment of reliability.")


class ProfileUpdate(BaseModel):
    summary: str = Field(description="One sentence (max 25 words) describing who this person is to the user.")
    relationship: str | None = Field(
        default=None, description="Relationship to the user if known, e.g. 'college friend', 'manager', 'sister'."
    )
    aliases: list[str] = Field(default_factory=list, description="Nicknames or other names the person goes by.")
    tags: list[str] = Field(default_factory=list, description="3-8 short lowercase tags (interests, contexts).")
    profile_markdown: str = Field(
        description="The complete updated profile body in Markdown, starting with '# <Name>'. "
        "No YAML frontmatter and no 'Source Log' section."
    )
    changes: list[str] = Field(default_factory=list, description="Short bullets describing what changed in this update.")

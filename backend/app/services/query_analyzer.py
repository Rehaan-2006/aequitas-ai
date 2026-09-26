# NOTE(scope): no conversation memory / multi-turn context here, by design.
# This agent is stateless and classifies one query in isolation; threading
# context across turns is a Module 7 (run_pipeline) orchestration concern.
# Also out of scope: pipeline routing -- this agent only classifies and
# flags, it does not decide what downstream modules do with the result
# (e.g. it does NOT skip retrieval for FALSE_PREMISE queries).
"""
Module 2 -- Query Analyzer Agent.

Classifies an already-sanitized user query (post Module 1.5) as
FACTUAL, FALSE_PREMISE, or EXPLORATORY, flagging the specific false
premise when one is present.
"""

from enum import Enum

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.core.config import settings


class QueryType(str, Enum):
    FACTUAL = "factual"
    FALSE_PREMISE = "false_premise"
    EXPLORATORY = "exploratory"


class QueryAnalysis(BaseModel):
    query_type: QueryType
    reasoning: str = Field(description="Brief justification for the classification.")
    flagged_premise: str | None = Field(
        default=None,
        description="What is factually/legally wrong with the query's premise. "
        "Populated only when query_type is FALSE_PREMISE, otherwise null.",
    )


SYSTEM_PROMPT = """You are the Query Analyzer for a legal research assistant. \
Classify the user's legal research question into exactly one category:

- FACTUAL: a well-formed legal question with no false assumptions (e.g. asking \
what the law is, what a case held, what a standard requires).
- FALSE_PREMISE: the question assumes something false or outdated as true -- \
e.g. treating an overruled case as good law, misstating a holding, assuming a \
statute/rule exists or reads a certain way when it does not.
- EXPLORATORY: broad, open-ended, or comparative questions with no single false \
or true premise to check (e.g. "what are the arguments for and against X").

Do not answer the legal question itself. Only classify it. When the type is \
FALSE_PREMISE, state precisely what premise is wrong in flagged_premise; \
otherwise leave flagged_premise null."""


# `defer_model_check` means the "openrouter:<model>" string is only resolved
# into a real Model (and only then needs OPENROUTER_API_KEY) on first actual
# run -- not at import time, and never at all when a test overrides the
# model via `query_analyzer_agent.override(model=...)`.
query_analyzer_agent: Agent[None, QueryAnalysis] = Agent(
    f"openrouter:{settings.query_analyzer_model}",
    output_type=QueryAnalysis,
    system_prompt=SYSTEM_PROMPT,
    defer_model_check=True,
)


def analyze_query(query: str) -> QueryAnalysis:
    """
    Public entry point. Stateless, single-query classification -- no
    memory of prior calls, no pipeline-routing decisions.
    """
    result = query_analyzer_agent.run_sync(query)
    return result.output

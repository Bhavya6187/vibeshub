"""Output schema for the trace digest agent.

The Digest model is what the OpenAI call must return as a JSON object.
Validation failures are not retried — the pipeline records the failure
in agent_run and the upload still succeeds without a digest.
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, Field


_EM_DASH_RE = re.compile(r"\s*—\s*")


def strip_em_dashes(text: str) -> str:
    """Replace U+2014 em-dashes with ', ' so digests never ship with them.

    The user has a standing preference against em-dashes in vibeshub
    user-facing copy. The model occasionally emits them; we sweep on
    persist rather than relying on prompt engineering alone.
    """
    return _EM_DASH_RE.sub(", ", text)


class FileNote(BaseModel):
    path: str
    caption: str = Field(max_length=140)


class Chapter(BaseModel):
    anchor_uuid: str
    title: str = Field(max_length=80)
    caption: str = Field(max_length=160)


_Item = Annotated[str, Field(max_length=200)]


class Digest(BaseModel):
    ask: str = Field(max_length=200)
    decisions: list[_Item] = Field(default_factory=list, max_length=6)
    dead_ends: list[_Item] = Field(default_factory=list, max_length=4)
    learnings: list[_Item] = Field(default_factory=list, max_length=5)
    tests: str = Field(max_length=200)
    chapters: list[Chapter] = Field(default_factory=list, max_length=10)
    file_notes: list[FileNote] = Field(default_factory=list, max_length=20)

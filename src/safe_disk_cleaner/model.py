"""Data model: categories of reclaimable space and the items inside them."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Risk(Enum):
    SAFE = ("Safe", "risk-safe",
            "Nothing you need is lost. The system recreates or re-downloads it if it is ever needed again.")
    CAUTION = ("Usually safe", "risk-caution",
               "Nothing personal is lost, but something will be slower or need re-downloading the next time.")
    REVIEW = ("Review first", "risk-review",
              "Could contain something you still want. Look through the list before cleaning.")

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def css(self) -> str:
        return self.value[1]

    @property
    def meaning(self) -> str:
        return self.value[2]

    @property
    def rank(self) -> int:
        return {"SAFE": 0, "CAUTION": 1, "REVIEW": 2}[self.name]


@dataclass
class Explanation:
    what: str             # What is it?
    why: str              # Why does it grow?
    safe: str             # Is it safe to delete, and what happens afterwards?
    how: str              # What exactly the app does
    tip: str = ""         # How to stop it from growing again


@dataclass
class Choice:
    label: str
    value: str
    size: int


@dataclass
class Item:
    id: str
    title: str
    size: int
    subtitle: str = ""
    risk: Risk = Risk.SAFE
    selected_by_default: bool = False
    # What cleaning this item does; see cleaner.build_plan for the kinds.
    action: dict = field(default_factory=dict)
    # Shown in warning colour; if blocked the item cannot be selected.
    note: str = ""
    blocked: bool = False
    # Optional alternatives (e.g. "shrink journal to 200 MB / 500 MB").
    choices: list[Choice] = field(default_factory=list)
    default_choice: int = 0


@dataclass
class Setting:
    """A 'stop it from growing' option that runs immediately when applied."""
    id: str
    title: str
    subtitle: str
    choices: list[tuple[str, str]]       # (label, value)
    current: str                         # value currently in effect, "" if unknown
    op: str                              # helper operation name
    param: str                           # parameter name for the chosen value


@dataclass
class Category:
    id: str
    title: str
    icon: str
    risk: Risk
    explanation: Explanation
    items: list[Item] = field(default_factory=list)
    available: bool = True
    unavailable_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    settings: list[Setting] = field(default_factory=list)
    needs_admin: bool = False
    size_is_estimate: bool = False
    hidden_small: int = 0          # number of tiny entries not listed
    error: str = ""

    @property
    def total(self) -> int:
        return sum(i.choices[i.default_choice].size if i.choices else i.size for i in self.items)

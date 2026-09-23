import enum
from dataclasses import dataclass


class Sort(enum.StrEnum):
    RANK = "rank"
    DATE = "date"


class Operator(enum.StrEnum):
    EQUAL = "="
    NOT_EQUAL = "!="
    CONTAINS = "~"
    GREATER = ">"
    LESS = "<"


@dataclass
class Criteria:
    field: str
    operator: Operator
    value: str


@dataclass
class AdvancedSearch:
    criteria: list[Criteria]
    limit: int = 10

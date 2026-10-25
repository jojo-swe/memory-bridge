"""Strict TOML configuration: misspelled settings fail before allocation."""

from dataclasses import fields
from pathlib import Path
import tomllib

from .core import Limits


def load_limits(path) -> Limits:
    with Path(path).open("rb") as source:
        document = tomllib.load(source)
    if set(document) != {"memory"} or not isinstance(document["memory"], dict):
        raise ValueError("Configuration must contain exactly one [memory] table")
    values = document["memory"]
    allowed = {field.name for field in fields(Limits)}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError("Unknown memory settings: " + ", ".join(sorted(unknown)))
    return Limits(**values)

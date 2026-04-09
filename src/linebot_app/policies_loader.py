from __future__ import annotations

from pathlib import Path


def load_trusted_domains(*, defaults: tuple[str, ...]) -> tuple[str, ...]:
    policies_file = Path(__file__).resolve().parent / "policies" / "trusted_domains.txt"
    if not policies_file.exists():
        return defaults

    file_text = policies_file.read_text(encoding="utf-8")
    lines = [line.strip().lower() for line in file_text.splitlines()]
    domains = tuple(line for line in lines if line and not line.startswith("#"))
    return domains or defaults

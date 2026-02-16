from __future__ import annotations

DEFAULT_NON_GOALS = ["No fallbacks/retries/guardrails unless acceptance requires it"]


def _toml_string(s: str) -> str:
    s2 = str(s).replace('"', "").replace("\n", " ").strip()
    return f'"{s2}"'


def _toml_list_str(xs: list[str]) -> str:
    out = ["["]
    for x in xs:
        out.append(f"  {_toml_string(str(x))},")
    out.append("]")
    return "\n".join(out)


def format_default_contract_block(*, mode: str, objective: str, touch: list[str]) -> str:
    lines: list[str] = []
    lines.append("```wg-contract")
    lines.append("schema = 1")
    lines.append(f"mode = {_toml_string(mode)}")
    lines.append(f"objective = {_toml_string(objective)}")
    lines.append(f"non_goals = {_toml_list_str(DEFAULT_NON_GOALS)}")
    lines.append(f"touch = {_toml_list_str(list(touch or []))}")
    lines.append("acceptance = []")
    lines.append("max_files = 25")
    lines.append("max_loc = 800")
    lines.append("pit_stop_after = 3")
    lines.append("auto_followups = true")
    lines.append("```")
    return "\n".join(lines).rstrip() + "\n"

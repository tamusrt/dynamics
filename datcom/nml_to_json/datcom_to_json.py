import json
import re
from pathlib import Path


GROUP_RE = re.compile(r"\$([A-Za-z]\w*)\s*(.*?)\$", re.DOTALL)
ASSIGNMENT_RE = re.compile(r"\b([A-Za-z]\w*)(?:\((\d+)\))?\s*=", re.IGNORECASE)
REPEAT_RE = re.compile(r"^(\d+)\*(.+)$")
CONTROL_CARDS_KEY = "__control_cards__"
ORDER_KEY = "__order__"


def _split_tokens(raw_values: str) -> list[str]:
    flat = raw_values.replace("\n", " ").replace("\r", " ")
    return [token.strip() for token in flat.split(",") if token.strip()]


def _coerce_scalar(token: str):
    if (token.startswith("'") and token.endswith("'")) or (
        token.startswith('"') and token.endswith('"')
    ):
        return token[1:-1]

    upper = token.upper()
    if upper in {"T", ".TRUE.", "TRUE"}:
        return True
    if upper in {"F", ".FALSE.", "FALSE"}:
        return False

    if re.fullmatch(r"[+-]?\d+", token):
        return int(token)

    normalized = re.sub(r"[dD]", "e", token)
    try:
        return float(normalized)
    except ValueError:
        return token


def _parse_value_list(raw_values: str) -> list:
    values = []
    for token in _split_tokens(raw_values):
        repeat_match = REPEAT_RE.match(token)
        if repeat_match:
            count = int(repeat_match.group(1))
            repeated_value = _coerce_scalar(repeat_match.group(2).strip())
            values.extend([repeated_value] * count)
        else:
            values.append(_coerce_scalar(token))
    return values


def _parse_group_body(group_body: str) -> dict:
    group_data = {}
    assignments = list(ASSIGNMENT_RE.finditer(group_body))

    for idx, match in enumerate(assignments):
        var_name = match.group(1).lower()
        index_text = match.group(2)

        value_start = match.end()
        value_end = assignments[idx + 1].start() if idx + 1 < len(assignments) else len(group_body)
        raw_values = group_body[value_start:value_end].strip().strip(",")
        parsed_values = _parse_value_list(raw_values)

        if index_text:
            start_index = int(index_text) - 1
            existing = group_data.get(var_name)
            if isinstance(existing, list):
                target = existing
            elif existing is None:
                target = []
            else:
                target = [existing]

            needed_size = start_index + len(parsed_values)
            if len(target) < needed_size:
                target.extend([None] * (needed_size - len(target)))

            target[start_index : start_index + len(parsed_values)] = parsed_values
            group_data[var_name] = target
        else:
            group_data[var_name] = parsed_values[0] if len(parsed_values) == 1 else parsed_values

    return group_data


def _extract_control_cards(text_segment: str) -> list[str]:
    control_cards = []
    for raw_line in text_segment.splitlines():
        line = raw_line.strip()
        if line:
            control_cards.append(line)
    return control_cards


def datcom_nml_to_dict(input_path: str) -> dict:
    text = Path(input_path).read_text(encoding="utf-8")
    result = {}
    control_cards = []
    order = []
    cursor = 0

    for group_match in GROUP_RE.finditer(text):
        preceding_segment = text[cursor : group_match.start()]
        for card in _extract_control_cards(preceding_segment):
            control_cards.append(card)
            order.append({"type": "control_card", "index": len(control_cards) - 1})

        group_name = group_match.group(1).lower()
        group_body = group_match.group(2)
        result[group_name] = _parse_group_body(group_body)
        order.append({"type": "group", "name": group_name})
        cursor = group_match.end()

    trailing_segment = text[cursor:]
    for card in _extract_control_cards(trailing_segment):
        control_cards.append(card)
        order.append({"type": "control_card", "index": len(control_cards) - 1})

    if control_cards:
        result[CONTROL_CARDS_KEY] = control_cards
    if order:
        result[ORDER_KEY] = order

    return result


if __name__ == "__main__":
    nml_dict = datcom_nml_to_dict("input.nml")
    with open("output.json", "w", encoding="utf-8") as output_file:
        json.dump(nml_dict, output_file, indent=4)
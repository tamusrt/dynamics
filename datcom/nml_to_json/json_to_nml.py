import argparse
import copy
import json
import re
from pathlib import Path


IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
CONTROL_CARDS_KEY = "__control_cards__"
ORDER_KEY = "__order__"
FLTCON_KEY = "fltcon"
MAX_MACH_PER_CASE = 20
MAX_ALPHA_PER_CASE = 20


def _format_scalar(value):
    if isinstance(value, bool):
        return "T" if value else "F"

    if isinstance(value, int):
        return f"{value}."

    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value)}."
        return format(value, ".15g")

    if isinstance(value, str):
        upper = value.upper()
        if IDENT_RE.fullmatch(upper):
            return upper
        escaped = value.replace("'", "''")
        return f"'{escaped}'"

    return json.dumps(value)


def _format_value_list(values):
    return ",".join(_format_scalar(item) for item in values)


def _list_to_assignment_chunks(var_name, values):
    chunks = []
    run_start = None

    for idx, item in enumerate(values):
        if item is None:
            if run_start is not None:
                run_values = values[run_start:idx]
                chunks.append((run_start, run_values))
                run_start = None
            continue

        if run_start is None:
            run_start = idx

    if run_start is not None:
        chunks.append((run_start, values[run_start:]))

    if not chunks:
        return []

    assignments = []
    for start_idx, run_values in chunks:
        if start_idx == 0:
            lhs = var_name
        else:
            lhs = f"{var_name}({start_idx + 1})"
        rhs = _format_value_list(run_values)
        assignments.append(f"{lhs}={rhs},")

    return assignments


def _group_to_lines(group_name, group_data):
    lines = [f"${group_name.upper()}"]

    for raw_key, value in group_data.items():
        key = raw_key.upper()

        if value is None:
            continue

        if isinstance(value, list):
            assignments = _list_to_assignment_chunks(key, value)
            for assignment in assignments:
                lines.append(f"  {assignment}")
            continue

        lines.append(f"  {key}={_format_scalar(value)},")

    if len(lines) > 1:
        lines[-1] = f"{lines[-1]}$"
    else:
        lines.append("$")
    return lines


def _iter_sections(data):
    order = data.get(ORDER_KEY)
    control_cards = data.get(CONTROL_CARDS_KEY, [])

    if isinstance(order, list):
        for item in order:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type == "group":
                group_name = item.get("name")
                group_data = data.get(group_name)
                if isinstance(group_name, str) and isinstance(group_data, dict):
                    yield ("group", group_name, group_data)
            elif item_type == "control_card":
                index = item.get("index")
                if isinstance(index, int) and 0 <= index < len(control_cards):
                    card = control_cards[index]
                    if isinstance(card, str) and card.strip():
                        yield ("control_card", card.strip(), None)
        return

    for group_name, group_data in data.items():
        if group_name in {CONTROL_CARDS_KEY, ORDER_KEY}:
            continue
        if isinstance(group_data, dict):
            yield ("group", group_name, group_data)

    if isinstance(control_cards, list):
        for card in control_cards:
            if isinstance(card, str) and card.strip():
                yield ("control_card", card.strip(), None)


def _to_list(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _chunk_values(values, chunk_size):
    return [values[idx : idx + chunk_size] for idx in range(0, len(values), chunk_size)]


def _build_case_fltcon(base_fltcon, mach_values, alpha_values, beta_value):
    case_fltcon = {
        "nalpha": len(alpha_values),
        "nmach": len(mach_values),
        "mach": list(mach_values),
        "alpha": list(alpha_values),
        "beta": beta_value,
        "alt": [0.0] * len(mach_values),
    }

    extras = {}
    for key, value in base_fltcon.items():
        lower_key = key.lower()
        if lower_key in {"nmach", "nalpha", "mach", "alpha", "beta", "alt"}:
            continue
        extras[key] = copy.deepcopy(value)

    case_fltcon.update(extras)
    return case_fltcon


def _build_case_fltcon_list(data):
    fltcon = data.get(FLTCON_KEY)
    if not isinstance(fltcon, dict):
        return None

    mach_values = _to_list(fltcon.get("mach"))
    alpha_values = _to_list(fltcon.get("alpha"))
    beta_values = _to_list(fltcon.get("beta"))

    if not mach_values or not alpha_values or not beta_values:
        return None

    mach_chunks = _chunk_values(mach_values, MAX_MACH_PER_CASE)
    alpha_chunks = _chunk_values(alpha_values, MAX_ALPHA_PER_CASE)

    case_fltcon_list = []
    for beta_value in beta_values:
        for mach_chunk in mach_chunks:
            for alpha_chunk in alpha_chunks:
                case_fltcon_list.append(_build_case_fltcon(fltcon, mach_chunk, alpha_chunk, beta_value))

    return case_fltcon_list


def json_to_nml_dict(data):
    sections = list(_iter_sections(data))
    case_fltcon_list = _build_case_fltcon_list(data)

    output_lines = []
    if case_fltcon_list is None:
        for section_type, name_or_line, group_data in sections:
            if section_type == "group":
                output_lines.extend(_group_to_lines(name_or_line, group_data))
            elif section_type == "control_card":
                output_lines.append(name_or_line)
        return "\n".join(output_lines) + "\n"

    fltcon_start_idx = None
    for idx, (section_type, name_or_line, _group_data) in enumerate(sections):
        if section_type == "group" and str(name_or_line).lower() == FLTCON_KEY:
            fltcon_start_idx = idx
            break

    if fltcon_start_idx is None:
        for section_type, name_or_line, group_data in sections:
            if section_type == "group":
                output_lines.extend(_group_to_lines(name_or_line, group_data))
            elif section_type == "control_card":
                output_lines.append(name_or_line)
        return "\n".join(output_lines) + "\n"

    global_sections = sections[:fltcon_start_idx]
    case_sections = sections[fltcon_start_idx:]

    for section_type, name_or_line, group_data in global_sections:
        if section_type == "group":
            output_lines.extend(_group_to_lines(name_or_line, group_data))
        elif section_type == "control_card":
            output_lines.append(name_or_line)

    for case_fltcon in case_fltcon_list:
        for section_type, name_or_line, group_data in case_sections:
            if section_type == "group":
                if name_or_line.lower() == FLTCON_KEY:
                    output_lines.extend(_group_to_lines(name_or_line, case_fltcon))
                else:
                    output_lines.extend(_group_to_lines(name_or_line, group_data))
            elif section_type == "control_card":
                output_lines.append(name_or_line)

    return "\n".join(output_lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Convert DATCOM JSON back to NML namelist format.")
    parser.add_argument("input_json", nargs="?", default="input.json", help="Path to input JSON file")
    parser.add_argument("output_nml", nargs="?", default="for005.dat", help="Path to output NML file")
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output_nml)

    with input_path.open("r", encoding="utf-8") as infile:
        data = json.load(infile)

    nml_text = json_to_nml_dict(data)

    with output_path.open("w", encoding="utf-8") as outfile:
        outfile.write(nml_text)


if __name__ == "__main__":
    main()
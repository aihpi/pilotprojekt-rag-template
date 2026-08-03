"""Declarative field-mapping engine for the JSON/CSV parsers.

Turns records into ``Section``s via a small DSL (see :class:`config.schema.FieldMapping`).

Error philosophy: YAML authors have no debugger, so *structural* mistakes
(``record_path`` / iterate-step ``path`` missing or pointing at the wrong type)
raise :class:`ConfigMappingError` with the full path, the expected vs. actual
type, and a one-line syntax example. Field references inside ``text_template`` /
``metadata`` are lenient (missing -> empty) — validate those with ``--dry-run``.
"""

from __future__ import annotations

import string
from typing import Any

from kb.parsers.base import Section

_FMT = string.Formatter()


class ConfigMappingError(ValueError):
    """A structural error in a JSON/CSV field-mapping config."""


def _fail(
    source_name: str,
    where: str,
    dotted: str,
    expected: str,
    found: Any,
    example: str,
    *,
    detail: str = "",
) -> "ConfigMappingError":
    if isinstance(found, dict):
        actual = f"dict (keys: {', '.join(map(str, list(found)[:12])) or '<empty>'})"
    else:
        actual = type(found).__name__
    msg = (
        f"data source '{source_name}': {where} '{dotted}': expected {expected}, "
        f"but found {actual}."
    )
    if detail:
        msg += f" {detail}"
    msg += f"\n  Correct syntax, e.g.:  {example}"
    return ConfigMappingError(msg)


def _resolve_path_strict(
    obj: Any, dotted: str, *, source_name: str, where: str, example: str
) -> Any:
    """Walk a dotted path, raising a rich error on a missing key / wrong type."""
    cur = obj
    parts = dotted.split(".")
    for i, part in enumerate(parts):
        if not isinstance(cur, dict):
            at = ".".join(parts[:i]) or "<root>"
            raise _fail(
                source_name, where, dotted, "an object to descend into", cur, example,
                detail=f"'{at}' is not an object.",
            )
        if part not in cur:
            raise _fail(
                source_name, where, dotted, f"key '{part}' to exist", cur, example,
                detail=f"missing at '{'.'.join(parts[: i + 1])}'.",
            )
        cur = cur[part]
    return cur


def _resolve_path_lenient(namespace: dict[str, Any], dotted: str) -> Any:
    """Best-effort dotted lookup used for template/metadata fields. Missing -> None."""
    cur: Any = namespace
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _render_template(template: str, namespace: dict[str, Any]) -> str:
    out: list[str] = []
    for literal, field, spec, conv in _FMT.parse(template):
        out.append(literal)
        if field is None:
            continue
        value = _resolve_path_lenient(namespace, field)
        out.append("" if value is None else str(value))
    return "".join(out)


def _resolve_value(spec: Any, namespace: dict[str, Any], source_name: str) -> Any:
    """Resolve a metadata value spec: dotted path, ``@bound_key``, {const}, {template}."""
    if isinstance(spec, dict):
        if "const" in spec:
            return spec["const"]
        if "template" in spec:
            return _render_template(str(spec["template"]), namespace)
        raise ConfigMappingError(
            f"data source '{source_name}': metadata value {spec!r} must be a field name, "
            f"'@bound_key', {{const: ...}} or {{template: '...'}}."
        )
    if isinstance(spec, str):
        key = spec[1:] if spec.startswith("@") else spec
        return _resolve_path_lenient(namespace, key)
    return spec


def _build_metadata(mapping_meta: dict[str, Any], namespace: dict[str, Any], source_name: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for key, spec in mapping_meta.items():
        value = _resolve_value(spec, namespace, source_name)
        if value is not None and value != "":
            meta[key] = value
    return meta


def _text_from(mapping, namespace: dict[str, Any]) -> str:
    if mapping.text_template:
        return _render_template(mapping.text_template, namespace).strip()
    if mapping.text_fields:
        parts = [
            str(_resolve_path_lenient(namespace, f) or "").strip()
            for f in mapping.text_fields
        ]
        return "\n\n".join(p for p in parts if p).strip()
    # Default: join all scalar values of the namespace.
    return "\n".join(
        str(v).strip() for v in namespace.values() if isinstance(v, (str, int, float)) and str(v).strip()
    ).strip()


def _section_from(mapping, namespace, source_name, fallback_id) -> Section | None:
    text = _text_from(mapping, namespace)
    if not text:
        return None
    doc_id = None
    if mapping.id_template:
        doc_id = _render_template(mapping.id_template, namespace).strip() or None
    return Section(
        text=text,
        doc_id=doc_id or fallback_id,
        metadata={
            "source": source_name,
            **_build_metadata(mapping.metadata, namespace, source_name),
        },
    )


# --------------------------------------------------------------------------- #
# Nested iteration (record_specs)
# --------------------------------------------------------------------------- #
def _walk(current: Any, steps: list, i: int, namespace: dict[str, Any], source_name: str):
    if i == len(steps):
        yield dict(namespace)
        return
    step = steps[i]
    keys = step.path if isinstance(step.path, list) else [step.path]
    multi = isinstance(step.path, list)
    example = "- {path: items, as: item}   (path must point at a list)"
    for key in keys:
        if not isinstance(current, dict):
            raise _fail(
                source_name, "iterate step path", str(key), "an object to read the key from",
                current, example,
            )
        if key not in current:
            if multi:
                continue  # a sibling key (e.g. an absent 'erhoeht' level) — skip
            raise _fail(
                source_name, "iterate step path", str(key), f"key '{key}' to exist",
                current, example,
            )
        child = current[key]
        if step.object:
            if not isinstance(child, dict):
                raise _fail(
                    source_name, "iterate step path", str(key), "an object (object: true)",
                    child, "- {path: anforderungen, object: true}",
                )
            ns = dict(namespace)
            if step.as_:
                ns[step.as_] = child
            if step.bind_key_as:
                ns[step.bind_key_as] = key
            yield from _walk(child, steps, i + 1, ns, source_name)
        else:
            if not isinstance(child, list):
                raise _fail(
                    source_name, "iterate step path", str(key), "a list to iterate",
                    child, example,
                )
            for elem in child:
                ns = dict(namespace)
                if step.as_:
                    ns[step.as_] = elem
                if step.bind_key_as:
                    ns[step.bind_key_as] = key
                yield from _walk(elem, steps, i + 1, ns, source_name)


def sections_from_record_specs(data: Any, mapping, source_name: str) -> list[Section]:
    sections: list[Section] = []
    seq = 0
    for spec in mapping.record_specs:
        for namespace in _walk(data, spec.iterate, 0, {}, source_name):
            section = _section_from(spec, namespace, source_name, f"{source_name}:{seq}")
            seq += 1
            if section is not None:
                sections.append(section)
    return sections


# --------------------------------------------------------------------------- #
# Flat records (record_path) and CSV rows
# --------------------------------------------------------------------------- #
def _records_from_flat(data: Any, mapping, source_name: str) -> list[dict[str, Any]]:
    example = "record_path: data.items   (must point at a list of objects)"
    if mapping.record_path:
        records = _resolve_path_strict(
            data, mapping.record_path, source_name=source_name,
            where="record_path", example=example,
        )
    else:
        records = data
    if not isinstance(records, list):
        raise _fail(
            source_name, "record_path", mapping.record_path or "<root>",
            "a list of records", records, example,
        )
    return records


def sections_from_records(records: list[dict[str, Any]], mapping, source_name: str) -> list[Section]:
    sections: list[Section] = []
    for seq, record in enumerate(records):
        ns = record if isinstance(record, dict) else {"value": record}
        section = _section_from(mapping, ns, source_name, f"{source_name}:{seq}")
        if section is not None:
            sections.append(section)
    return sections

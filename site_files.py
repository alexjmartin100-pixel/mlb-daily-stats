"""Write the JSON and text files the dashboard loads on demand."""

import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _clean(value):
    """Strict JSON: NaN/Infinity become null, numpy scalars unwrap."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            converted = item()
        except Exception:
            converted = value
        else:
            if converted is not value:
                return _clean(converted)
    return str(value)


def _atomic_write(directory, filename, text):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return path


def write_json(filename, obj):
    """Write data/<filename> as compact JSON."""
    import json
    text = json.dumps(_clean(obj), separators=(",", ":"), allow_nan=False)
    return _atomic_write(os.path.join(HERE, "data"), filename, text)


def write_text(folder, filename, text):
    return _atomic_write(os.path.join(HERE, folder), filename, text)

"""Todos los textos que pasan por tr(): los literales del código y los datos que se traducen."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "konvi"


def code_keys():
    keys = set()
    for f in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


def data_keys():
    from konvi.core import presets
    from konvi.ui import preset_dialog
    keys = set(presets.QUALITY_NAMES)
    for c in presets.CATEGORIES:
        keys |= {c.label, c.tooltip}
    for p in presets.BUILTIN:
        keys |= {p.name, p.description}
    for table in (preset_dialog.RESOLUTIONS, preset_dialog.FPS, preset_dialog.IMAGE_FPS,
                  preset_dialog.SPEEDS):
        keys |= {text for text, _ in table}
    from konvi.core import look
    from konvi.ui import seqview
    keys |= set(look.PRESETS) | set(seqview.LookPanel.TITLES.values())
    keys |= set(seqview.CurveEditor.TIPS.values()) | set(seqview.BalanceEditor.ZONE_TIPS.values())
    keys |= {name for _, name in seqview.BalanceEditor.ZONES}
    for label, tip in seqview.BalanceEditor.AXES:
        keys |= {label, tip}
    return keys


def all_keys():
    return code_keys() | data_keys()

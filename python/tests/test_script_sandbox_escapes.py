"""The script sandbox against the ways Python code reaches the rest of the system.

Each escape below, on the old full-Python sandbox, handed a pasted script the
real `os` module, the real builtins (open, eval, __import__) or the engine's
module globals — and from any of those, the user's credentials. Every one must
now be refused, either before the script compiles (validate) or when it runs
(ScriptError). The second half checks that the Python a strategy — or an AI
writing one — actually uses still works.
"""
from __future__ import annotations

import os

import pytest

import script_sandbox as ss

CWD = os.getcwd()


def _outcome(code: str):
    """'rejected: …' if validate refuses it, 'error' if running it fails,
    otherwise whatever decide() returned."""
    errors = ss.validate(code)
    if errors:
        return "rejected: " + "; ".join(errors)
    try:
        mod = ss.CompiledScript("escape", code)
        return mod.call("decide", {})
    except ss.ScriptError:
        return "error"


# Every escape tries to hand back the working directory via os.getcwd(), the
# real open(), or a real module. A result equal to CWD, or any module or
# builtin function object, means it got out.
ESCAPES = {
    "plain import": "import os\ndef decide(ctx):\n    return os.getcwd()\n",
    "from import": "from os import getcwd\ndef decide(ctx):\n    return getcwd()\n",
    "dunder import": "def decide(ctx):\n    return __import__('os').getcwd()\n",
    "builtins by name": "def decide(ctx):\n    return __builtins__['open']\n",
    "open": "def decide(ctx):\n    return open('/etc/hostname').read()\n",
    "eval": "def decide(ctx):\n    return eval(\"__import__('os').getcwd()\")\n",
    "exec": "def decide(ctx):\n    exec(\"import os\")\n",
    "compile": "def decide(ctx):\n    return compile('1', 'x', 'eval')\n",
    "vars and globals": "def decide(ctx):\n    return vars()\n",
    "module attribute that is a module":
        "def decide(ctx):\n    return statistics.sys.modules['os'].getcwd()\n",
    "allowed module's private attribute":
        "import random\ndef decide(ctx):\n    return random._os.getcwd()\n",
    "getattr to a module's private":
        "import random\ndef decide(ctx):\n    return getattr(random, '_os').getcwd()\n",
    "json.codecs.open": "import json\ndef decide(ctx):\n    return json.codecs.open\n",
    "subclasses walk":
        "def decide(ctx):\n    return ().__class__.__base__.__subclasses__()\n",
    "subclasses via getattr":
        "def decide(ctx):\n    return getattr(getattr((), '__class__'), '__base__')\n",
    "reduce over getattr":
        "import functools\n"
        "def decide(ctx):\n"
        "    return functools.reduce(getattr, ['__class__', '__base__'], ())\n",
    "builtin __self__": "def decide(ctx):\n    return len.__self__.open\n",
    "builtin __self__ via getattr": "def decide(ctx):\n    return getattr(len, '__self__')\n",
    "function globals": "def decide(ctx):\n    return decide.__globals__\n",
    "print globals": "def decide(ctx):\n    return print.__globals__\n",
    "closure": "def decide(ctx):\n    return log.__closure__\n",
    "generator frame":
        "def decide(ctx):\n"
        "    g = (x for x in [1])\n"
        "    return g.gi_frame.f_back.f_globals\n",
    "traceback frame":
        "def decide(ctx):\n"
        "    try:\n"
        "        1 / 0\n"
        "    except ZeroDivisionError as e:\n"
        "        return e.__traceback__.tb_frame.f_globals\n",
    "typing.get_type_hints eval":
        "import typing\n"
        "class C:\n"
        "    x: \"__import__('os').getcwd()\"\n"
        "def decide(ctx):\n"
        "    return typing.get_type_hints(C)\n",
    "from typing import get_type_hints":
        "from typing import get_type_hints\ndef decide(ctx):\n    return None\n",
    "operator.attrgetter": "import operator\ndef decide(ctx):\n    return operator\n",
    "string.Formatter": "import string\ndef decide(ctx):\n    return string\n",
    "importlib": "import importlib\ndef decide(ctx):\n    return importlib.import_module('os')\n",
    "sys": "import sys\ndef decide(ctx):\n    return sys.modules['os'].getcwd()\n",
    "relative import": "from . import db\ndef decide(ctx):\n    return db\n",
    "star import": "from math import *\ndef decide(ctx):\n    return pi\n",
    "module view internals": "def decide(ctx):\n    return type(math).__getattr__\n",
    "system exit": "def decide(ctx):\n    raise SystemExit(0)\n",
}


@pytest.mark.parametrize("name", list(ESCAPES))
def test_escape_is_refused(name):
    out = _outcome(ESCAPES[name])
    assert out != CWD, f"{name}: reached the filesystem"
    assert not callable(out) or isinstance(out, type), f"{name}: returned {out!r}"
    assert out in ("error",) or str(out).startswith("rejected:"), f"{name}: returned {out!r}"


def test_the_refusal_names_what_is_not_available():
    errors = ss.validate("def decide(ctx):\n    return decide.__globals__\n")
    assert errors == ["line 2: .__globals__ is not available to scripts"]


def test_modules_are_read_only_views():
    mod = ss.CompiledScript("ro", "def decide(ctx):\n    math.pi = 3\n")
    with pytest.raises(ss.ScriptError):
        mod.call("decide", {})
    import math
    assert math.pi != 3


# ------------------------------------------------------------ still works

WORKS = {
    "future annotations":
        "from __future__ import annotations\n"
        "def decide(ctx) -> dict | None:\n"
        "    return {'ok': True}\n",
    "stdlib the docs promise":
        "import datetime, json, re, random\n"
        "from collections import deque, Counter\n"
        "from typing import Optional, Dict\n"
        "def decide(ctx):\n"
        "    d: Optional[Dict[str, int]] = {'a': 1}\n"
        "    q = deque([1, 2, 3], maxlen=2)\n"
        "    return {'ok': json.dumps(d) == '{\"a\": 1}' and list(q) == [2, 3]\n"
        "            and bool(re.match(r'\\d+', '42')) and Counter('aab')['a'] == 2\n"
        "            and datetime.timedelta(minutes=3).total_seconds() == 180.0}\n",
    "injected math and statistics":
        "def decide(ctx):\n"
        "    return {'ok': math.floor(statistics.mean([1, 2, 4])) == 2}\n",
    "submodule import":
        "import collections.abc\n"
        "def decide(ctx):\n"
        "    return {'ok': isinstance({}, collections.abc.Mapping)}\n",
    "classes, super and privates":
        "class Base:\n"
        "    def __init__(self, n):\n"
        "        self._n = n\n"
        "        self.__secret = n * 2\n"
        "    def secret(self):\n"
        "        return self.__secret\n"
        "class Model(Base):\n"
        "    def __init__(self):\n"
        "        super().__init__(3)\n"
        "    def __repr__(self):\n"
        "        return f'{type(self).__name__}({self._n})'\n"
        "def decide(ctx):\n"
        "    m = Model()\n"
        "    return {'ok': repr(m) == 'Model(3)' and m.secret() == 6\n"
        "            and m.__class__.__name__ == 'Model'}\n",
    "dataclasses and enums":
        "from dataclasses import dataclass, field\n"
        "from enum import Enum\n"
        "class Side(Enum):\n"
        "    UP = 'up'\n"
        "@dataclass\n"
        "class Plan:\n"
        "    side: Side\n"
        "    sizes: list = field(default_factory=list)\n"
        "def decide(ctx):\n"
        "    p = Plan(Side.UP)\n"
        "    p.sizes.append(5)\n"
        "    return {'ok': p.side.value == 'up' and p.sizes == [5]}\n",
    "getattr, hasattr and setattr on data":
        "class Box:\n"
        "    pass\n"
        "def decide(ctx):\n"
        "    b = Box()\n"
        "    setattr(b, 'size', 5)\n"
        "    return {'ok': getattr(b, 'size') == 5 and hasattr(b, 'size')\n"
        "            and getattr(ctx, 'missing', None) is None}\n",
    "exceptions and generators":
        "def gen(n):\n"
        "    for i in range(n):\n"
        "        yield i * i\n"
        "def decide(ctx):\n"
        "    try:\n"
        "        {}['x']\n"
        "    except KeyError:\n"
        "        pass\n"
        "    return {'ok': sum(gen(4)) == 14}\n",
    "name guard":
        "def decide(ctx):\n"
        "    return {'ok': __name__ == 'rom_script'}\n",
}


@pytest.mark.parametrize("name", list(WORKS))
def test_ordinary_strategy_python_still_works(name):
    assert ss.validate(WORKS[name]) == [], name
    mod = ss.CompiledScript("works", WORKS[name])
    assert mod.call("decide", {}) == {"ok": True}, name


def test_shipped_examples_still_compile():
    root = os.path.join(os.path.dirname(__file__), "..", "..", "examples")
    names = [n for n in os.listdir(root) if n.endswith(".py")]
    assert names
    for n in names:
        with open(os.path.join(root, n), encoding="utf-8") as fh:
            code = fh.read()
        assert ss.validate(code) == [], n
        ss.CompiledScript(n, code)

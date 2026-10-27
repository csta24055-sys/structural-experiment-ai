"""Excel の数式をプログラムで再計算するための小さな評価器。

Excel が保存した計算結果（キャッシュ値）とは独立に、セルの値から数式を計算し直して照合する。
対応していない関数・構文は Unsupported を送出し、呼び出し側は「未検証」として記録する
（対応していないものを「一致」と扱わない）。
"""
from __future__ import annotations

import math
import re
import statistics
from typing import Any, Callable

ERRORS = ("#DIV/0!", "#REF!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!")


class Unsupported(Exception):
    pass


class FormulaError(Exception):
    """数式の評価中に Excel のエラー値に相当する状態になった。"""


_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ref>(?:(?:'[^']+'|[A-Za-z_][\w.]*)!)?\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)
  | (?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<func>[A-Za-z][A-Za-z0-9.]*(?=\())
  | (?P<bool>TRUE|FALSE)
  | (?P<op><=|>=|<>|[-+*/^&=<>%(),:])
""", re.VERBOSE)


def tokenize(s: str) -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(s):
        m = _TOKEN.match(s, i)
        if not m:
            raise Unsupported(f"解釈できない文字: {s[i:i + 10]!r}")
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group()))
        i = m.end()
    return out


def col_to_num(c: str) -> int:
    n = 0
    for ch in c.upper():
        n = n * 26 + ord(ch) - 64
    return n


def num_to_col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def split_ref(ref: str, default_sheet: str) -> tuple[str, str]:
    if "!" in ref:
        sh, cell = ref.rsplit("!", 1)
        sh = sh.strip("'")
    else:
        sh, cell = default_sheet, ref
    return sh, cell.replace("$", "").upper()


def expand_range(a: str, b: str) -> list[str]:
    ma, mb = re.match(r"([A-Z]+)(\d+)", a), re.match(r"([A-Z]+)(\d+)", b)
    c1, r1, c2, r2 = col_to_num(ma.group(1)), int(ma.group(2)), col_to_num(mb.group(1)), int(mb.group(2))
    if (abs(c2 - c1) + 1) * (abs(r2 - r1) + 1) > 100000:
        raise Unsupported("範囲が大きすぎる")
    return [f"{num_to_col(c)}{r}" for r in range(min(r1, r2), max(r1, r2) + 1) for c in range(min(c1, c2), max(c1, c2) + 1)]


def _nums(args: list) -> list[float]:
    out = []
    for a in args:
        if isinstance(a, list):
            out += [v for v in a if isinstance(v, (int, float)) and not isinstance(v, bool)]
        elif isinstance(a, bool):
            out.append(float(a))
        elif isinstance(a, (int, float)):
            out.append(a)
        elif a is None:
            continue
        else:
            try:
                out.append(float(a))
            except (TypeError, ValueError):
                raise FormulaError("#VALUE!") from None
    return out


def _pairs(ys, xs) -> tuple[list[float], list[float]]:
    if not isinstance(ys, list) or not isinstance(xs, list) or len(ys) != len(xs):
        raise FormulaError("#N/A")
    p = [(float(x), float(y)) for y, x in zip(ys, xs) if isinstance(x, (int, float)) and isinstance(y, (int, float))]
    return [x for x, _ in p], [y for _, y in p]


def _slope(ys, xs):
    x, y = _pairs(ys, xs)
    if len(x) < 2:
        raise FormulaError("#DIV/0!")
    mx, my = sum(x) / len(x), sum(y) / len(y)
    d = sum((a - mx) ** 2 for a in x)
    if d == 0:
        raise FormulaError("#DIV/0!")
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / d


def _intercept(ys, xs):
    x, y = _pairs(ys, xs)
    return sum(y) / len(y) - _slope(ys, xs) * sum(x) / len(x)


def _round(x, n=0, mode="half"):
    q = 10 ** int(n)
    v = float(x) * q
    if mode == "up":
        r = math.ceil(abs(v)) * (1 if v >= 0 else -1)
    elif mode == "down":
        r = math.floor(abs(v)) * (1 if v >= 0 else -1)
    else:
        r = math.floor(abs(v) + 0.5) * (1 if v >= 0 else -1)  # Excel は四捨五入（偶数丸めではない）
    return r / q


def _div0(f):
    def g(*a):
        try:
            return f(*a)
        except ZeroDivisionError:
            raise FormulaError("#DIV/0!") from None
        except statistics.StatisticsError:
            raise FormulaError("#DIV/0!") from None
    return g


FUNCS: dict[str, Callable] = {
    "SUM": lambda *a: sum(_nums(a)),
    "AVERAGE": _div0(lambda *a: statistics.fmean(_nums(a))),
    "MIN": lambda *a: min(_nums(a)) if _nums(a) else 0,
    "MAX": lambda *a: max(_nums(a)) if _nums(a) else 0,
    "COUNT": lambda *a: len(_nums([[v for v in x if isinstance(v, (int, float))] if isinstance(x, list) else x for x in a])),
    "PRODUCT": lambda *a: math.prod(_nums(a)),
    "MEDIAN": _div0(lambda *a: statistics.median(_nums(a))),
    "STDEV": _div0(lambda *a: statistics.stdev(_nums(a))),
    "STDEV.S": _div0(lambda *a: statistics.stdev(_nums(a))),
    "STDEV.P": _div0(lambda *a: statistics.pstdev(_nums(a))),
    "VAR": _div0(lambda *a: statistics.variance(_nums(a))),
    "VAR.S": _div0(lambda *a: statistics.variance(_nums(a))),
    "ABS": lambda x: abs(float(x)),
    "SQRT": lambda x: math.sqrt(float(x)) if float(x) >= 0 else (_ for _ in ()).throw(FormulaError("#NUM!")),
    "POWER": lambda x, y: float(x) ** float(y),
    "EXP": lambda x: math.exp(float(x)),
    "LN": lambda x: math.log(float(x)),
    "LOG10": lambda x: math.log10(float(x)),
    "LOG": lambda x, b=10: math.log(float(x), float(b)),
    "PI": lambda: math.pi,
    "SIN": lambda x: math.sin(float(x)), "COS": lambda x: math.cos(float(x)), "TAN": lambda x: math.tan(float(x)),
    "RADIANS": lambda x: math.radians(float(x)), "DEGREES": lambda x: math.degrees(float(x)),
    "ROUND": lambda x, n=0: _round(x, n), "ROUNDUP": lambda x, n=0: _round(x, n, "up"),
    "ROUNDDOWN": lambda x, n=0: _round(x, n, "down"),
    "SLOPE": _slope, "INTERCEPT": _intercept,
    "IF": lambda c, a=True, b=False: a if c else b,
}


class Evaluator:
    """resolve(sheet, cell) → セルの値（数値・文字列・None）。数式セルの場合も保存値を返す想定。"""

    def __init__(self, resolve: Callable[[str, str], Any], sheet: str):
        self.resolve = resolve
        self.sheet = sheet
        self.refs: list[tuple[str, str]] = []

    def evaluate(self, formula: str) -> Any:
        if not formula.startswith("="):
            raise Unsupported("数式ではない")
        self.toks = tokenize(formula[1:])
        self.i = 0
        v = self._compare()
        if self.i != len(self.toks):
            raise Unsupported(f"解釈できない構文: {formula}")
        if isinstance(v, list):
            raise Unsupported("範囲そのものを値として返す数式")
        return v

    # 構文: compare > concat > add > mul > power > unary > percent > primary
    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def _eat(self, val=None):
        t = self._peek()
        if val is not None and t[1] != val:
            raise Unsupported(f"'{val}' が必要")
        self.i += 1
        return t

    def _compare(self):
        a = self._concat()
        while self._peek()[1] in ("=", "<>", "<", ">", "<=", ">="):
            op = self._eat()[1]
            b = self._concat()
            a = {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        return a

    def _concat(self):
        a = self._add()
        while self._peek()[1] == "&":
            self._eat()
            a = f"{'' if a is None else a}{'' if (b := self._add()) is None else b}"
        return a

    @staticmethod
    def _num(v):
        if v is None:
            return 0.0
        if isinstance(v, bool):
            return float(v)
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str) and v in ERRORS:
            raise FormulaError(v)
        try:
            return float(v)
        except (TypeError, ValueError):
            raise FormulaError("#VALUE!") from None

    def _add(self):
        a = self._mul()
        while self._peek()[1] in ("+", "-"):
            op = self._eat()[1]
            b = self._mul()
            a = self._num(a) + self._num(b) if op == "+" else self._num(a) - self._num(b)
        return a

    def _mul(self):
        a = self._pow()
        while self._peek()[1] in ("*", "/"):
            op = self._eat()[1]
            b = self._pow()
            if op == "*":
                a = self._num(a) * self._num(b)
            else:
                if self._num(b) == 0:
                    raise FormulaError("#DIV/0!")
                a = self._num(a) / self._num(b)
        return a

    def _pow(self):
        a = self._unary()
        while self._peek()[1] == "^":
            self._eat()
            a = self._num(a) ** self._num(self._unary())
        return a

    def _unary(self):
        if self._peek()[1] == "-":
            self._eat()
            return -self._num(self._unary())
        if self._peek()[1] == "+":
            self._eat()
            return self._unary()
        v = self._primary()
        while self._peek()[1] == "%":
            self._eat()
            v = self._num(v) / 100
        return v

    def _primary(self):
        kind, val = self._eat()
        if kind == "num":
            return float(val)
        if kind == "str":
            return val[1:-1].replace('""', '"')
        if kind == "bool":
            return val == "TRUE"
        if kind == "ref":
            if ":" in val:
                a, b = val.split(":")
                sh, ca = split_ref(a, self.sheet)
                _, cb = split_ref(b, sh)
                cells = expand_range(ca, cb)
                self.refs += [(sh, c) for c in cells]
                return [self._cell(sh, c, in_range=True) for c in cells]
            sh, c = split_ref(val, self.sheet)
            self.refs.append((sh, c))
            return self._cell(sh, c)
        if kind == "func":
            name = val.upper()
            if name.startswith("_XLFN."):
                name = name[6:]
            self._eat("(")
            args = []
            if self._peek()[1] != ")":
                args.append(self._compare())
                while self._peek()[1] == ",":
                    self._eat()
                    args.append(self._compare())
            self._eat(")")
            f = FUNCS.get(name)
            if f is None:
                raise Unsupported(f"未対応の関数: {name}")
            try:
                return f(*args)
            except (FormulaError, Unsupported):
                raise
            except TypeError as e:
                raise Unsupported(f"{name} の引数を解釈できない: {e}") from e
            except (ValueError, OverflowError) as e:
                raise FormulaError("#NUM!") from e
        if val == "(":
            v = self._compare()
            self._eat(")")
            return v
        raise Unsupported(f"解釈できない要素: {val}")

    def _cell(self, sh: str, c: str, in_range: bool = False):
        v = self.resolve(sh, c)
        if isinstance(v, str) and v in ERRORS and not in_range:
            raise FormulaError(v)
        return v


def close_enough(a: Any, b: Any, rel: float = 1e-9, abs_tol: float = 1e-9) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=abs_tol)
    return str(a) == str(b)

#!/usr/bin/env python3
"""Printable ancestor chart (pedigree poster) from the ftb MCP server.

Subcommands
  collect  ROOT_ID  --out tree.json [--depth N] [--locale L]
                                                 pull ancestors through the ftb MCP server
  summary  tree.json                              counts, leaves and A1 fit per depth
  table    tree.json [--flagged-only] [--depth N] markdown verification table (chart language)
  set      tree.json AHN FIELD VALUE              correct one field (all occurrences of the person)
  render   tree.json --out chart.svg [--depth N]  SVG (+ PDF next to it)

Nodes are keyed by Ahnentafel number: subject 1, father 2n, mother 2n+1. A person reached
through two lines (pedigree collapse) is simply present under two numbers.

Everything printed on the chart or in the table comes from a locale file (locales/*.json,
or any JSON file with the same keys), chosen at `collect` and stored in tree.json.
Console messages are for the agent and stay in English.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------- locale

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"


def _read_locale(spec: str) -> dict:
    path = Path(spec)
    if not path.is_file():
        path = LOCALE_DIR / f"{spec}.json"
    if not path.is_file():
        names = ", ".join(sorted(p.stem for p in LOCALE_DIR.glob("*.json")))
        sys.exit(f"Unknown locale '{spec}': give a bundled one ({names}) or a path to a JSON file.")
    return json.loads(path.read_text())


def load_locale(spec: str) -> dict:
    """Locale by bundled name ('cs') or file path; missing keys are filled from English."""
    base, loc = _read_locale("en"), _read_locale(spec)
    missing = [k for k in base if k not in loc]
    for k, v in base.items():
        if isinstance(v, dict) and isinstance(loc.get(k), dict):
            missing += [f"{k}.{kk}" for kk in v if kk not in loc[k]]
            loc[k] = {**v, **loc[k]}
        else:
            loc.setdefault(k, v)
    for k in ("months", "months_short"):
        if len(loc[k]) != 12:
            sys.exit(f"Locale '{spec}': '{k}' must list 12 months.")
    if missing:
        print(
            f"Locale '{spec}': missing keys filled from English: {', '.join(missing)}",
            file=sys.stderr,
        )
    return loc


def tree_locale(tree: dict) -> dict:
    # version 1 trees predate locales and were always Czech
    return tree.get("locale") or load_locale("cs")


# ----------------------------------------------------------------------------- dates

# month words recognized in the *source* data: GEDCOM and every bundled locale
GED_MONTHS = {
    m: i
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
        start=1,
    )
}
for _f in sorted(LOCALE_DIR.glob("*.json")):
    _l = json.loads(_f.read_text())
    for _i, _m in [
        *enumerate(_l["months"], start=1),
        *enumerate(_l["months_short"], start=1),
        *((v, k) for k, v in _l.get("month_aliases", {}).items()),
    ]:
        GED_MONTHS.setdefault(_m.upper().rstrip("."), _i)
QUALIFIERS = [  # longest spelling first; the GEDCOM importer writes ABOUT, FTB writes ABT
    (
        ("ABOUT", "ABT", "CIRCA", "CA.", "CA", "CALCULATED", "CAL", "ESTIMATED", "EST", "KOL."),
        "about",
    ),
    (("BEFORE", "BEF"), "before"),
    (("AFTER", "AFT"), "after"),
    (("FROM",), "from"),
    (("TO",), "to"),
]


def _simple_date(s: str, loc: dict) -> str | None:
    """'4 MAY 1882' -> '4 May 1882' (en) / '4.5.1882' (cs), 'MAY 1882', '1882'."""
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{3,4})", s)
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
    else:
        t = [w.rstrip(".") if not w[:-1].isdigit() else w for w in s.replace(".", ". ").split()]
        if t and t[0].endswith(".") and t[0][:-1].isdigit():
            t[0] = t[0][:-1]
        if len(t) == 3 and t[0].isdigit() and t[1] in GED_MONTHS and t[2].isdigit():
            d, mo, y = int(t[0]), GED_MONTHS[t[1]], int(t[2])
        elif len(t) == 2 and t[0] in GED_MONTHS and t[1].isdigit():
            d, mo, y = None, GED_MONTHS[t[0]], int(t[1])
        elif len(t) == 1 and t[0].isdigit():
            return str(int(t[0]))
        else:
            return None
    if not 1 <= mo <= 12:
        return None
    parts = {
        "m": mo,
        "mm": f"{mo:02}",
        "y": y,
        "month": loc["months"][mo - 1],
        "mon": loc["months_short"][mo - 1],
    }
    if d is None:
        return loc["month_year"].format(**parts)
    return loc["date"].format(d=d, dd=f"{d:02}", **parts)


def format_date(date: dict | None, loc: dict) -> tuple[str, bool]:
    """Display form of an MCP date object in the chart language. Returns (text, needs_review)."""
    if not date:
        return "", False
    raw = (date.get("display") or "").strip()
    if not raw:
        return (str(date["year"]), False) if date.get("year") else ("", False)
    s = re.sub(r"\s+", " ", raw.upper())
    m = re.fullmatch(r"(?:BETWEEN|BET|FROM) (.+?) (?:AND|TO) (.+)", s)
    if m:
        a, b = _simple_date(m.group(1), loc), _simple_date(m.group(2), loc)
        if a and b:
            return loc["qualifiers"]["between"].format(a=a, b=b), False
    for prefixes, kind in QUALIFIERS:
        for prefix in prefixes:
            if s.startswith(prefix + " "):
                inner = _simple_date(s[len(prefix) + 1 :], loc)
                if inner:
                    return loc["qualifiers"][kind].format(date=inner), False
    simple = _simple_date(s, loc)
    if simple:
        return simple, False
    return raw, True


# ----------------------------------------------------------------------------- places

# Cleanup rules work on the *source* data, so they know the languages genealogy sources are
# commonly written in, whatever language the chart is printed in.
SUSPICIOUS_PLACE = re.compile(
    r"/|\?|\bnemocnic|\bokr(?:es|\.)|\bu [A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]|\b\w{1,2}\.\s?\w{1,2}\."
    r"|\bhospital|\bnear\b|\bbei\b|\bKrankenhaus|\bSpital"
)
PLACE_ALIAS = re.compile(
    r",|\s*/?\s*\b(?:nyní|dnes|též|neboli|now|formerly|aka|jetzt|heute)\b"
    r"|\s+-\s+"
)
HOUSE_NO = re.compile(r"\b(?:č\.\s*p\.|čp\.|č\.|Nr\.|No\.)\s*(?=\d)")


def normalize_place(raw: str | None) -> tuple[str, bool]:
    """Parentheses dropped, first non-empty comma segment, 'č.p. 12' -> '12'. (text, review).

    A trailing '?' marks an uncertain place in the source and is kept. Values that still
    look like two places or carry a description are flagged for the agent to judge.
    """
    if not raw:
        return "", False
    p = re.sub(r"\s*\([^)]*\)?", "", raw)
    # first non-empty comma segment ("X, County, State" and ", , , Ireland"); drop aliases
    # and descriptions ("X now Y", "X též Y", "X - mill")
    p = next((v for v in (v.strip(" /") for v in PLACE_ALIAS.split(p)) if v), "")
    p = HOUSE_NO.sub("", p)
    p = re.sub(r"\s+", " ", p).strip()
    return p, p != raw.strip() or bool(SUSPICIOUS_PLACE.search(p))


# ----------------------------------------------------------------------------- names

VARIANT_SEP = re.compile(
    r"\s*(?:/|\(|\)|,|\btéž\b|\bneboli\b|\balias\b|\baka\b|\bvel\b|\brecte\b)\s*"
)


def _fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.casefold()) if unicodedata.category(c) != "Mn"
    )


def _variants(s: str, given: bool) -> list[str]:
    """Name variants; dash placeholders ('(-----)') are dropped, '?' is kept, and an
    all-capitals surname ('KENNEDY') is title-cased."""
    variants = [v for v in VARIANT_SEP.split(s) if v and not re.fullmatch(r"[-_.]+", v)]
    if not given:
        variants = [v.title() if v.isupper() and len(v) > 1 else v for v in variants]
    return variants


def pick_variant(s: str, given: bool, loc: dict) -> tuple[str, bool]:
    """'Agnes / Anežka' -> ('Anežka', True) in Czech, 'Smith (Smyth)' -> ('Smith', True).

    Prefers the chart language's form: a word with one of the locale's preferred letters,
    or (for given names) a name on the locale's list; otherwise the first variant.
    A multi-word first name ('Mary Anne') is one variant.
    """
    s = re.sub(r"\s+", " ", (s or "").strip())
    variants = _variants(s, given)
    if len(variants) <= 1:
        return (variants[0] if variants else ""), bool(s) and (not variants or variants[0] != s)
    letters, names = set(loc["preferred_letters"]), set(loc["preferred_given_names"])

    def native(v: str) -> bool:
        return bool(letters & set(v)) or (
            given and bool(names) and all(w in names for w in v.split())
        )

    chosen = next((v for v in variants if native(v)), variants[0])
    return chosen, True


# ----------------------------------------------------------------------------- collect


def _find_mcp_config(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / ".mcp.json").is_file():
            return d / ".mcp.json"
    sys.exit("No .mcp.json found here or above - pass --mcp-config PATH.")


async def _collect(args: argparse.Namespace) -> dict:
    from contextlib import AsyncExitStack

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    cfg_path = _find_mcp_config(args.mcp_config)
    servers = json.loads(cfg_path.read_text())["mcpServers"]
    if args.server not in servers:
        sys.exit(f"Server '{args.server}' is not in {cfg_path} (have: {', '.join(servers)}).")
    srv = servers[args.server]

    async with AsyncExitStack() as stack:
        if srv.get("type", "stdio") == "stdio":
            params = StdioServerParameters(
                command=srv["command"],
                args=srv.get("args", []),
                env=srv.get("env") or None,
                cwd=str(cfg_path.parent),
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        else:
            import mcp.client.streamable_http as h

            client = getattr(h, "streamable_http_client", None) or h.streamablehttp_client
            streams = await stack.enter_async_context(client(srv["url"]))
            read, write = streams[0], streams[1]
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        async def call(tool: str, **kw: Any) -> dict:
            res = await session.call_tool(tool, kw)
            text = "".join(getattr(c, "text", "") for c in res.content)
            if getattr(res, "isError", False) or getattr(res, "is_error", False):
                sys.exit(f"{tool}({kw}) failed: {text}")
            return json.loads(text)

        pedigree = await call("get_ancestors", person_id=args.root_id, generations=args.depth)
        flat: dict[int, dict] = {}

        def walk(n: dict) -> None:
            flat[n["ahnentafel"]] = n
            for k in ("father", "mother"):
                if k in n:
                    walk(n[k])

        walk(pedigree["root"])
        profiles = {}
        for pid in sorted({n["person_id"] for n in flat.values()}):
            profiles[pid] = await call("get_person", person_id=pid, include=["facts", "families"])
    return build_tree(args.root_id, args.depth, flat, profiles, load_locale(args.locale))


def _fact(person: dict, *tags: str) -> tuple[dict | None, str | None]:
    for tag in tags:
        for f in person.get("facts", []):
            if f.get("gedcom_tag") == tag and (f.get("date") or f.get("place")):
                return f, tag
    return None, None


def _name_parts(p: dict) -> tuple[str, str]:
    """Title before the first name and suffix after the surname, as they appear in 'name'."""
    name, first, last = p.get("name") or "", p.get("first_name") or "", p.get("last_name") or ""
    title = name[: name.find(first)].strip() if first and name.find(first) > 0 else ""
    at = name.rfind(last) if last else -1
    suffix = name[at + len(last) :].strip() if at >= 0 else ""
    return title, suffix


def build_tree(root_id: int, depth: int, flat: dict[int, dict], profiles: dict, loc: dict) -> dict:
    nodes: dict[str, dict] = {}
    first_seen: dict[int, int] = {}
    for ahn in sorted(flat):
        s = flat[ahn]
        p = profiles[s["person_id"]]
        # review notes are [code, args]; the table words them in the chart language
        review: list[list] = []
        gender = {"male": "M", "female": "F"}.get(p.get("gender"), "U")
        if gender == "U":
            review.append(["unknown_gender", {}])

        title, suffix = _name_parts(p)
        first, ch = pick_variant(p.get("first_name") or "", given=True, loc=loc)
        if ch:
            review.append(["given_variant", {"raw": p.get("first_name")}])
        surname, ch = pick_variant(p.get("last_name") or "", given=False, loc=loc)
        child = nodes.get(str(ahn // 2))
        if ch and ahn % 2 == 0 and child:
            # a father's surname variant must match the one his child carries
            def stem(v: str) -> str:
                return _fold(v)[: max(3, min(len(v), 5) - 1)]

            match = [
                v
                for v in _variants(p.get("last_name") or "", given=False)
                if _fold(child["surname"]).startswith(stem(v))
            ]
            if match:
                surname = match[0]
        if ch:
            review.append(["surname_variant", {"raw": p.get("last_name")}])

        node: dict[str, Any] = {
            "ahn": ahn,
            "gen": s["generation"],
            "person_id": s["person_id"],
            "gender": gender,
            "title": title,
            "first_name": first,
            "surname": surname,
            "suffix": suffix,
            "living": p.get("living_status") == "living",
            "has_more_ancestors": bool(s.get("has_more_ancestors")),
            "raw": {
                "name": p.get("name"),
                "first_name": p.get("first_name"),
                "last_name": p.get("last_name"),
            },
        }
        for key, tags, fallback in (
            ("birth", ("BIRT", "CHR", "BAPM"), "baptism_used"),
            ("death", ("DEAT", "BURI"), "burial_used"),
        ):
            f, tag = _fact(p, *tags)
            if key == "death" and node["living"]:
                f = None
            d, bad = format_date(f and f.get("date"), loc)
            pl, changed = normalize_place(f and f.get("place"))
            node[f"{key}_date"], node[f"{key}_place"] = d, pl
            node["raw"][key] = f and {
                "tag": tag,
                "date": (f.get("date") or {}).get("display"),
                "place": f.get("place"),
            }
            if f and tag != tags[0]:
                review.append([fallback, {}])
            if bad:
                review.append(["date_unparsed", {"event": key, "value": d}])
            if changed:
                review.append(["place_changed", {"event": key, "raw": f.get("place")}])

        if s["person_id"] in first_seen:
            node["duplicate_of"] = first_seen[s["person_id"]]
        else:
            first_seen[s["person_id"]] = ahn
        node["review"] = review
        nodes[str(ahn)] = node

    # marriage of each child's parents (only the direct-line couple)
    for key, node in nodes.items():
        ahn = int(key)
        father, mother = flat.get(2 * ahn), flat.get(2 * ahn + 1)
        node["parents_marriage"] = ""
        if not (father and mother):
            continue
        fam = next(
            (
                f
                for f in profiles[father["person_id"]].get("families_as_spouse", [])
                if any(m["person_id"] == mother["person_id"] for m in f["members"])
            ),
            None,
        )
        marr = next(
            (f for f in (fam or {}).get("facts", []) if f.get("gedcom_tag") == "MARR"), None
        )
        d, bad = format_date(marr and marr.get("date"), loc)
        node["parents_marriage"] = d
        node["raw"]["parents_marriage"] = marr and (marr.get("date") or {}).get("display")
        if bad:
            node["review"].append(["marriage_unparsed", {"value": d}])

    root = nodes["1"]
    return {
        "version": 2,
        "root_person_id": root_id,
        "depth_requested": depth,
        "title": default_title(root, loc),
        "locale": loc,
        "nodes": nodes,
    }


# ----------------------------------------------------------------------------- helpers


def load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def save(tree: dict, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(tree, ensure_ascii=False, indent=1) + "\n")


def trimmed(tree: dict, depth: int | None) -> dict[int, dict]:
    nodes = {int(k): v for k, v in tree["nodes"].items()}
    if depth is not None:
        nodes = {k: v for k, v in nodes.items() if v["gen"] <= depth}
    return nodes


def default_title(n: dict, loc: dict) -> str:
    """Title from the locale template; the agent puts the name into the right grammatical case."""
    name = " ".join(x for x in (n.get("first_name"), n.get("surname"), n.get("suffix")) if x)
    return loc["title"].format(name=name)


def display_name(n: dict) -> str:
    parts = [n.get("title"), n.get("first_name"), n.get("surname"), n.get("suffix")]
    return " ".join(p for p in parts if p) or "?"


def box_lines(n: dict, loc: dict) -> list[tuple[str, str]]:
    """(text, place) per box line; the place is split off so it can wrap onto its own line.

    An event line is omitted when both its date and place are unknown.
    """
    lines = [(display_name(n), "")]
    for prefix, key in ((loc["born"], "birth"), (loc["died"], "death")):
        date, place = n.get(f"{key}_date", ""), n.get(f"{key}_place", "")
        if date or place:
            lines.append((f"{prefix} {date or '?'}", place))
    return lines


def wrap_lines(
    lines: list[tuple[str, str]], fonts: Fonts, cap: float | None
) -> list[tuple[str, bool]]:
    """Move the place of an over-wide event line to its own indented line. -> (text, indented)."""
    out: list[tuple[str, bool]] = []
    for head, place in lines:
        line = f"{head}, {place}" if place else head
        if cap and place and fonts.width(line, F) + 2 * PAD_X > cap:
            out += [(head + ",", False), (place, True)]
        else:
            out.append((line, False))
    return out


def stats(nodes: dict[int, dict]) -> dict:
    anc = {k: v for k, v in nodes.items() if k > 1}
    gens = max((v["gen"] for v in nodes.values()), default=0)
    leaves = [k for k in anc if 2 * k not in nodes and 2 * k + 1 not in nodes]
    return {
        "ancestors": len(anc),
        "unique": len({v["person_id"] for v in anc.values()}),
        "generations": gens,
        "leaves": len(leaves),
        "leaves_oldest": sum(1 for k in leaves if nodes[k]["gen"] == gens),
        "extendable": sum(1 for k in leaves if nodes[k].get("has_more_ancestors")),
    }


# ----------------------------------------------------------------------------- layout

PAGE_SIZES = {
    "A0": (1189, 841),
    "A1": (841, 594),
    "A2": (594, 420),
    "A3": (420, 297),
    "A4": (297, 210),
}
F = 3.6  # base font size in mm (~10 pt before page scaling)
LH = 1.3 * F  # line height
PAD_X, PAD_Y = 2.4, 1.0
G = 12.0  # horizontal gap between generations; marriage line sits mid-gap
VGAP_COUPLE = 1.0  # vertical gap between a husband and wife
VGAP = 3.0  # vertical gap between boxes of different couples
INDENT = 1.2 * F  # wrapped continuation line (the place moved to its own line)
FL = 0.85 * F  # marriage label font size
CENTER_GAP = 8.0  # horizontal gap between the father's and the mother's box
BAR_GAP = 4.0  # parents' joining bar -> subject box
MARGIN = 15.0
MAX_SCALE = 3.0

COLORS = {"M": ("#cfe2f6", "#7aa3cc"), "F": ("#f9d6e3", "#d696ae"), "U": ("#e8e8e8", "#9a9a9a")}


class Fonts:
    def __init__(self) -> None:
        self.family = "Noto Sans"
        self._fonts = {}
        self._cache: dict[tuple[str, bool], float] = {}
        try:
            from PIL import ImageFont

            for bold in (False, True):
                style = "Bold" if bold else "Regular"
                out = subprocess.run(
                    ["fc-match", "-f", "%{file}|%{family}", f"Noto Sans:style={style}"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                file, family = out.split("|", 1)
                self._fonts[bold] = ImageFont.truetype(file, 100)
                if not bold:
                    self.family = family.split(",")[0]
        except Exception:  # no Pillow / fontconfig: fall back to an average glyph width
            self._fonts = {}

    def width(self, text: str, size: float, bold: bool = False) -> float:
        key = (text, bold)
        if key not in self._cache:
            if bold in self._fonts:
                # +6 %: renderers (cairo hinting, PDF viewers, printers) set text a bit wider
                self._cache[key] = self._fonts[bold].getlength(text) / 100 * 1.06
            else:
                self._cache[key] = len(text) * (0.6 if bold else 0.55)
        return self._cache[key] * size


class Layout:
    def __init__(self, nodes: dict[int, dict], fonts: Fonts, loc: dict, cap: float | None = None):
        self.nodes, self.fonts = nodes, fonts
        self.lines = {k: wrap_lines(box_lines(v, loc), fonts, cap) for k, v in nodes.items()}
        self.w = {
            k: max(
                fonts.width(t, F, i == 0) + (INDENT if ind else 0) for i, (t, ind) in enumerate(ls)
            )
            + 2 * PAD_X
            for k, ls in self.lines.items()
        }
        self.h = {k: 2 * PAD_Y + len(ls) * LH for k, ls in self.lines.items()}
        gens = max(v["gen"] for v in nodes.values())
        self.col_w = {
            g: max([self.w[k] for k, v in nodes.items() if v["gen"] == g] + [30])
            for g in range(gens + 1)
        }
        # the subject hangs below its parents, so it must stay clear of generation 2
        w1 = self.col_w.get(1, 0)
        self.center_gap = max(CENTER_GAP, self.col_w[0] - 2 * (w1 + G) + 2 * VGAP)
        self.bar_y = 0.0
        self.y: dict[int, float] = {}
        self.x: dict[int, float] = {}  # box center
        self.side: dict[int, int] = {1: 0}
        self._place()

    def label_len(self, ahn: int) -> float:
        text = self.nodes[ahn].get("parents_marriage")
        return self.fonts.width(text, FL) + 2 if text else 0

    def _lay(self, ahn: int) -> tuple[dict[int, list[float]], dict[int, float]]:
        g = self.nodes[ahn]["gen"]
        contour = {g: [-self.h[ahn] / 2, self.h[ahn] / 2]}
        rel = {ahn: 0.0}
        parents = [p for p in (2 * ahn, 2 * ahn + 1) if p in self.nodes]
        if len(parents) == 2:
            (cf, rf), (cm, rm) = self._lay(parents[0]), self._lay(parents[1])
            d = max(
                [self.label_len(ahn)]
                + [
                    cf[c][1] - cm[c][0] + (VGAP_COUPLE if c == g + 1 else VGAP)
                    for c in cf.keys() & cm.keys()
                ]
            )
            shifts = ((cf, rf, -d / 2), (cm, rm, d / 2))
        elif parents:
            c, r = self._lay(parents[0])
            shifts = ((c, r, 0.0),)
        else:
            shifts = ()
        for c, r, dy in shifts:
            for gen, (top, bot) in c.items():
                cur = contour.setdefault(gen, [top + dy, bot + dy])
                cur[0], cur[1] = min(cur[0], top + dy), max(cur[1], bot + dy)
            rel.update({k: v + dy for k, v in r.items()})
        return contour, rel

    def inner_edge(self, g: int) -> float:
        """Distance from the chart axis to the near edge of generation g (g >= 1)."""
        return self.center_gap / 2 + sum(self.col_w[j] + G for j in range(1, g))

    def _place(self) -> None:
        # parents side by side on the axis line y=0, the subject centered below them
        parents = [p for p in (2, 3) if p in self.nodes]
        self.bar_y = max([self.h[p] / 2 for p in parents], default=0) + FL + 2.5
        self.x[1] = 0.0
        self.y[1] = (self.bar_y + BAR_GAP + self.h[1] / 2) if parents else 0.0
        for start, sign in ((2, 1), (3, -1)):  # father right, mother left
            if start not in self.nodes:
                continue
            _, rel = self._lay(start)
            for k, dy in rel.items():
                g = self.nodes[k]["gen"]
                self.y[k] = dy
                self.side[k] = sign
                self.x[k] = sign * (self.inner_edge(g) + self.col_w[g] / 2)

    def box(self, k: int) -> tuple[float, float, float, float]:
        w = self.col_w[self.nodes[k]["gen"]]
        return self.x[k] - w / 2, self.y[k] - self.h[k] / 2, w, self.h[k]

    def bbox(self) -> tuple[float, float, float, float]:
        xs, ys = [], []
        for k in self.nodes:
            x, y, w, h = self.box(k)
            xs += [x, x + w]
            ys += [y, y + h]
        return min(xs), min(ys), max(xs), max(ys)


TITLE_F = 3.6 * F  # title font size (before page scaling)


def title_geometry(L: Layout, text: str | None) -> dict | None:
    """Framed title, preferably in the empty space above the parents (costs no height)."""
    if not text:
        return None
    x0, y0, x1, y1 = L.bbox()

    def frame(t: float) -> tuple[float, float]:
        return L.fonts.width(text, t, True) + 0.04 * t * len(text) + 3.2 * t, 2.2 * t

    if any(n["gen"] >= 2 for n in L.nodes.values()):
        # between the generation-1 marriage lines, clear of their rotated dates
        free_w = 2 * (L.center_gap / 2 + L.col_w[1] + G / 2 - 1.5 - FL)
    else:
        free_w = x1 - x0
    parents_top = min((L.y[p] - L.h[p] / 2 for p in (2, 3) if p in L.nodes), default=y0)
    room = parents_top - y0 - 2 * VGAP
    t = TITLE_F * min(1.0, free_w / frame(TITLE_F)[0])
    fw, fh = frame(t)
    if t >= 0.7 * TITLE_F and fh <= room:
        return {"size": t, "w": fw, "h": fh, "top": y0, "extra": 0.0}
    fw, fh = frame(TITLE_F)
    return {"size": TITLE_F, "w": fw, "h": fh, "top": y0 - fh - 2 * VGAP, "extra": fh + 2 * VGAP}


def fit(layout: Layout, page: str, orientation: str, title: str | None):
    x0, y0, x1, y1 = layout.bbox()
    tg = title_geometry(layout, title)
    title_h = tg["extra"] if tg else 0
    if tg:
        x0, x1 = min(x0, -tg["w"] / 2), max(x1, tg["w"] / 2)
    w, h = x1 - x0, y1 - y0 + title_h
    long, short = PAGE_SIZES[page]
    options = {"landscape": (long, short), "portrait": (short, long)}
    if orientation != "auto":
        options = {orientation: options[orientation]}
    best = None
    for name, (pw, ph) in options.items():
        s = min((pw - 2 * MARGIN) / w, (ph - 2 * MARGIN) / h, MAX_SCALE)
        if best is None or s > best[1]:
            best = (name, s, pw, ph)
    name, s, pw, ph = best
    return {
        "orientation": name,
        "scale": s,
        "page_w": pw,
        "page_h": ph,
        "font_pt": F * s / 0.3528,
        "bbox": (x0, y0 - title_h, x1, y1),
        "title": tg,
    }


def best_layout(
    nodes: dict[int, dict], fonts: Fonts, loc: dict, page: str, orientation: str, title: str | None
) -> tuple[Layout, dict]:
    """Try narrowing the widest boxes by wrapping their places; keep the largest print.

    Wrapping trades width for height, so it only pays off while the chart is limited by
    the page width. Trying a range of caps and measuring is simpler and safer than
    predicting which side binds.
    """
    best = None
    L = Layout(nodes, fonts, loc)
    widest = max(L.col_w.values())
    for cap in [None] + [widest - 3 * i for i in range(1, 40) if widest - 3 * i >= 35]:
        layout = L if cap is None else Layout(nodes, fonts, loc, cap)
        f = fit(layout, page, orientation, title)
        if best is None or f["font_pt"] > best[1]["font_pt"] + 0.05:
            best = (layout, f)
    return best


# ----------------------------------------------------------------------------- render

TITLE_INK, TITLE_PAPER = "#7a5a36", "#fffaf0"


def title_svg(text: str, tg: dict) -> str:
    """Title in a double frame with diamond ornaments and corner dots."""
    t, w, h, top = tg["size"], tg["w"], tg["h"], tg["top"]
    x, cy = -w / 2, top + h / 2
    d, u = 0.18 * t, 0.12 * t  # inner frame inset, ornament unit
    parts = [
        f'<g stroke="{TITLE_INK}" fill="none">',
        f'<rect x="{x:.2f}" y="{top:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{2 * u:.2f}" '
        f'fill="{TITLE_PAPER}" stroke-width="{0.08 * t:.2f}"/>',
        f'<rect x="{x + d:.2f}" y="{top + d:.2f}" width="{w - 2 * d:.2f}" '
        f'height="{h - 2 * d:.2f}" rx="{u:.2f}" stroke-width="{0.025 * t:.2f}"/>',
    ]
    for sx in (-1, 1):
        ex = sx * w / 2  # diamond on each side of the outer frame
        parts.append(
            f'<path d="M{ex - 1.6 * u:.2f},{cy:.2f}L{ex:.2f},{cy - 1.6 * u:.2f}'
            f'L{ex + 1.6 * u:.2f},{cy:.2f}L{ex:.2f},{cy + 1.6 * u:.2f}Z" '
            f'fill="{TITLE_PAPER}" stroke-width="{0.05 * t:.2f}"/>'
        )
        parts.append(
            f'<circle cx="{ex:.2f}" cy="{cy:.2f}" r="{0.5 * u:.2f}" fill="{TITLE_INK}" '
            f'stroke="none"/>'
        )
        for yy in (top + 2 * d, top + h - 2 * d):  # dots in the inner corners
            parts.append(
                f'<circle cx="{sx * (w / 2 - 2 * d):.2f}" cy="{yy:.2f}" '
                f'r="{0.35 * u:.2f}" fill="{TITLE_INK}" stroke="none"/>'
            )
    parts.append("</g>")
    parts.append(
        f'<text x="0" y="{cy + 0.36 * t:.2f}" font-size="{t:.2f}" font-weight="bold" '
        f'text-anchor="middle" fill="{TITLE_INK}" letter-spacing="{0.04 * t:.2f}">'
        f"{_esc(text)}</text>"
    )
    return "\n".join(parts)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg(tree: dict, nodes: dict[int, dict], args: argparse.Namespace) -> tuple[str, dict]:
    fonts = Fonts()
    title = None if args.no_title else (tree.get("title") or "")
    L, f = best_layout(nodes, fonts, tree_locale(tree), args.page, args.orientation, title)
    s, (bx0, by0, bx1, by1) = f["scale"], f["bbox"]
    tx = f["page_w"] / 2 - (bx0 + bx1) / 2 * s
    ty = f["page_h"] / 2 - (by0 + by1) / 2 * s
    family = f"'{fonts.family}', 'DejaVu Sans', sans-serif"

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{f["page_w"]}mm" '
        f'height="{f["page_h"]}mm" viewBox="0 0 {f["page_w"]} {f["page_h"]}">',
        f'<rect width="{f["page_w"]}" height="{f["page_h"]}" fill="#ffffff"/>',
        f'<g transform="translate({tx:.3f} {ty:.3f}) scale({s:.5f})" '
        f'font-family="{family}" fill="#222">',
    ]

    if f["title"]:
        out.append(title_svg(title, f["title"]))

    # connectors
    path = []
    for k in nodes:
        parents = [p for p in (2 * k, 2 * k + 1) if p in nodes]
        if not parents:
            continue
        x, y, w, h = L.box(k)
        if k == 1:
            # parents' boxes drop onto a horizontal bar; the subject hangs from its middle
            for p in parents:
                path.append(f"M{L.x[p]:.2f},{L.y[p] + L.h[p] / 2:.2f}V{L.bar_y:.2f}")
            xs = [L.x[p] for p in parents] + [0.0]
            path.append(f"M{min(xs):.2f},{L.bar_y:.2f}H{max(xs):.2f}M0,{L.bar_y:.2f}V{y:.2f}")
            if len(parents) == 2 and nodes[1].get("parents_marriage"):
                out.append(
                    f'<text x="0" y="{L.bar_y - 1.2:.2f}" font-size="{FL:.2f}" '
                    f'text-anchor="middle" fill="#555">'
                    f"{_esc(nodes[1]['parents_marriage'])}</text>"
                )
            continue
        sign = L.side[k]
        edge = x + w if sign > 0 else x
        g1 = nodes[k]["gen"] + 1
        par_edge = sign * L.inner_edge(g1)
        if len(parents) == 1:
            path.append(f"M{edge:.2f},{y + h / 2:.2f}H{par_edge:.2f}")
            continue
        xl = edge + sign * G / 2
        yf, ym, yc = L.y[parents[0]], L.y[parents[1]], L.y[k]
        path.append(f"M{edge:.2f},{yc:.2f}H{xl:.2f}")
        path.append(f"M{par_edge:.2f},{yf:.2f}H{xl:.2f}V{ym:.2f}H{par_edge:.2f}")
        label = nodes[k].get("parents_marriage")
        if label:
            # rotated -90°: glyphs extend to -x of the baseline, so offset per side
            bx = xl + 1.0 + 0.75 * FL if sign > 0 else xl - 1.0 - 0.22 * FL
            out.append(
                f'<text transform="translate({bx:.2f} {yc:.2f}) rotate(-90)" '
                f'font-size="{FL:.2f}" text-anchor="middle" fill="#555">'
                f"{_esc(label)}</text>"
            )
    out.append(
        f'<path d="{" ".join(path)}" fill="none" stroke="#666" stroke-width="0.35" '
        f'stroke-linejoin="round"/>'
    )

    # boxes
    for k, n in sorted(nodes.items()):
        x, y, w, h = L.box(k)
        fill, stroke = COLORS[n.get("gender", "U")]
        sw = 0.7 if k == 1 else 0.3
        out.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="1.5" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )
        tx_, anchor = (x + PAD_X, "start") if args.align == "left" else (x + w / 2, "middle")
        for i, (line, indented) in enumerate(L.lines[k]):
            weight = ' font-weight="bold"' if i == 0 else ""
            lx = tx_ + (INDENT if indented and anchor == "start" else 0)
            out.append(
                f'<text x="{lx:.2f}" y="{y + PAD_Y + i * LH + 0.98 * F:.2f}" '
                f'font-size="{F:.2f}" text-anchor="{anchor}"{weight}>{_esc(line)}</text>'
            )
    out.append("</g></svg>")
    return "\n".join(out) + "\n", f


# ----------------------------------------------------------------------------- commands


def cmd_collect(args: argparse.Namespace) -> None:
    import anyio

    tree = anyio.run(_collect, args)
    save(tree, args.out)
    print(f"Saved: {args.out} (locale: {tree['locale']['language']})")
    cmd_summary(argparse.Namespace(tree=args.out, page=args.page, depth=None))


def cmd_summary(args: argparse.Namespace) -> None:
    tree = load(args.tree)
    loc = tree_locale(tree)
    full = trimmed(tree, None)
    st = stats(full)
    scope = trimmed(tree, args.depth)
    print(f"Person: {display_name(full[1])} (id {full[1]['person_id']})")
    print(f"Ancestors: {st['ancestors']} (distinct people: {st['unique']})")
    print(f"Ancestor generations: {st['generations']}")
    print(
        f"Leaves (ancestors without known parents): {st['leaves']}, "
        f"in the oldest generation: {st['leaves_oldest']}"
    )
    if st["extendable"]:
        print(
            f"At the depth limit {tree['depth_requested']}, {st['extendable']} ancestors still "
            f"have known parents - the tree can be extended."
        )
    first = [v for v in scope.values() if not v.get("duplicate_of")]
    flagged = sum(1 for v in first if v.get("review"))
    edited = sum(1 for v in first if v.get("edits"))
    within = f" (up to depth {args.depth})" if args.depth else ""
    print(f"Records to check{within}: {flagged}" + (f", edited: {edited}" if edited else ""))
    print()
    print(f"| depth | ancestors | leaves | font on {args.page} |")
    print("|---|---|---|---|")
    fonts = Fonts()
    for d in range(1, st["generations"] + 1):
        nodes = trimmed(tree, d)
        sd = stats(nodes)
        _, f = best_layout(nodes, fonts, loc, args.page, "auto", tree.get("title"))
        warn = " ⚠ small" if f["font_pt"] < 6 else ""
        print(
            f"| {d} | {sd['ancestors']} | {sd['leaves']} | "
            f"{f['font_pt']:.1f} pt ({f['orientation']}){warn} |"
        )


MINOR_NOTES = {"given_variant", "surname_variant"}  # routine variant picks, summarized in one line


def note_text(note: list | str, loc: dict) -> str:
    if isinstance(note, str):  # version 1 trees stored ready-made Czech text
        return note
    code, a = note
    if "event" in a:
        a = {**a, "event": loc["events"][a["event"]]}
    return loc["notes"][code].format(**a)


def cmd_table(args: argparse.Namespace) -> None:
    tree = load(args.tree)
    loc = tree_locale(tree)
    nodes = trimmed(tree, args.depth)
    minor: list[str] = []
    h = loc["table"]
    rows = [
        "| "
        + " | ".join(
            h[c] for c in ("ahn", "gen", "name", "birth", "death", "parents_marriage", "review")
        )
        + " |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, n in sorted(nodes.items()):
        if n.get("duplicate_of"):  # same data as the first occurrence; keep the table short
            if not args.flagged_only:
                rows.append(
                    f"| {k} | {n['gen']} | {display_name(n)} | | | | "
                    f"{loc['notes']['duplicate'].format(ahn=n['duplicate_of'])} |"
                )
            continue
        review = n.get("review", [])
        notes = [note_text(x, loc) for x in review] + [
            loc["notes"]["edited"].format(field=loc["fields"].get(f, f), old=old, new=n.get(f, ""))
            for f, old in dict(n.get("edits") or {}).items()
        ]
        routine = all(not isinstance(x, str) and x[0] in MINOR_NOTES for x in review)
        if args.flagged_only and routine and not n.get("edits"):
            if notes:
                minor.append(f"{display_name(n)} ({h['ahn']} {k})")
            continue
        cells = [
            str(k),
            str(n["gen"]),
            display_name(n),
            ", ".join(x for x in (n.get("birth_date"), n.get("birth_place")) if x),
            ", ".join(x for x in (n.get("death_date"), n.get("death_place")) if x),
            n.get("parents_marriage", ""),
            "; ".join(notes),
        ]
        rows.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    text = "\n".join(rows) + "\n"
    if minor:
        text += (
            "\n"
            + loc["notes"]["minor_summary"].format(count=len(minor), names=", ".join(minor))
            + "\n"
        )
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"Table saved: {args.out} ({len(rows) - 2} rows)")
    else:
        print(text, end="")


EDITABLE = [
    "title",
    "first_name",
    "surname",
    "suffix",
    "gender",
    "birth_date",
    "birth_place",
    "death_date",
    "death_place",
    "parents_marriage",
]


def cmd_set(args: argparse.Namespace) -> None:
    tree = load(args.tree)
    if args.field == "chart_title":
        tree["title"] = args.value
        print(f'Set chart_title = "{args.value}"')
    else:
        if args.field not in EDITABLE:
            sys.exit(f"Field must be one of: chart_title, {', '.join(EDITABLE)}")
        target = tree["nodes"].get(str(args.ahn))
        if not target:
            sys.exit(f"Node {args.ahn} does not exist.")
        # pedigree collapse: every occurrence of the person must print the same
        hits = [n for n in tree["nodes"].values() if n["person_id"] == target["person_id"]]
        for n in hits:
            edits = n.setdefault("edits", {})
            if isinstance(edits, list):  # trees saved by an older version
                edits = n["edits"] = {}
            edits.setdefault(args.field, n.get(args.field, ""))  # keep the original value
            n[args.field] = args.value
        print(f'Set {args.field} = "{args.value}" on node {", ".join(str(n["ahn"]) for n in hits)}')
    save(tree, args.tree)


def cmd_render(args: argparse.Namespace) -> None:
    tree = load(args.tree)
    nodes = trimmed(tree, args.depth)
    svg, f = render_svg(tree, nodes, args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg)
    print(f"SVG: {out}")
    if not args.no_pdf:
        pdf = out.with_suffix(".pdf")
        try:
            import cairosvg

            cairosvg.svg2pdf(url=str(out), write_to=str(pdf))
            print(f"PDF: {pdf}")
        except ImportError:
            if shutil.which("rsvg-convert"):
                subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(pdf), str(out)], check=True)
                print(f"PDF: {pdf}")
            else:
                print("PDF not written: cairosvg is missing (run via `uv run --with cairosvg`).")
    print(
        f"Page {args.page} {f['orientation']}, scale {f['scale']:.2f}, "
        f"font {f['font_pt']:.1f} pt"
        + (" - WARNING: hard to read in print" if f["font_pt"] < 6 else "")
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect")
    p.add_argument("root_id", type=int)
    p.add_argument("--out", required=True)
    p.add_argument("--depth", type=int, default=15, help="generations, max 15 (MCP limit)")
    p.add_argument("--mcp-config")
    p.add_argument("--server", default="ftb")
    p.add_argument("--page", default="A1", choices=PAGE_SIZES)
    p.add_argument(
        "--locale",
        default="en",
        help="chart language: bundled name (en, cs, de) or path to a locale JSON",
    )
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("summary")
    p.add_argument("tree")
    p.add_argument("--depth", type=int, help="count records to verify only up to this depth")
    p.add_argument("--page", default="A1", choices=PAGE_SIZES)
    p.set_defaults(fn=cmd_summary)

    p = sub.add_parser("table")
    p.add_argument("tree")
    p.add_argument("--depth", type=int)
    p.add_argument("--flagged-only", action="store_true")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_table)

    p = sub.add_parser("set")
    p.add_argument("tree")
    p.add_argument("ahn", type=int)
    p.add_argument("field")
    p.add_argument("value")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("render")
    p.add_argument("tree")
    p.add_argument("--out", required=True)
    p.add_argument("--depth", type=int)
    p.add_argument("--page", default="A1", choices=PAGE_SIZES)
    p.add_argument("--orientation", default="auto", choices=["auto", "landscape", "portrait"])
    p.add_argument("--align", default="left", choices=["left", "center"])
    p.add_argument("--no-title", action="store_true")
    p.add_argument("--no-pdf", action="store_true")
    p.set_defaults(fn=cmd_render)

    args = ap.parse_args()
    if getattr(args, "depth", None) is not None and args.cmd == "collect":
        args.depth = max(1, min(args.depth, 15))
    args.fn(args)


if __name__ == "__main__":
    main()

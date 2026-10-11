---
name: ftb-print
description: Generate a printable ancestor chart (pedigree poster) for one person from the ftb genealogy MCP server - a centered vector diagram (SVG + PDF, single A1 page) with the person below their parents, the father's line spreading to the right, the mother's line to the left, pastel blue/pink boxes and marriage dates on the connecting lines, with dates, abbreviations and place names in the user's language. Use this whenever the user wants to print, draw, export or visualize someone's family tree, pedigree or ancestors from the ftb / Family Tree Builder / MyHeritage / GEDCOM data, in any language, e.g. "print a family tree poster for grandma", "pedigree chart of John Smith on A1", "vytiskni rodokmen", "erstelle eine Ahnentafel", "imprime l'arbre généalogique", even if they don't mention SVG, PDF or the word "print".
---

# ftb-print – printable ancestor chart

Produce a print-ready ancestor chart for one person, in the user's language.

The chart contains the person plus their direct ancestors only: no siblings, no children,
no spouses from other marriages. The person sits below their parents, and from the parents
outward every older generation sits further left (mother's line) or right (father's line).
Couples are joined by vertical lines with the marriage date rotated 90° beside them.

## Tooling

Everything bulky runs through `scripts/ftb_print.py`, so the ancestor data never has to pass
through the conversation:

```bash
FTBP="uv run -q --no-project --with mcp --with pillow --with cairosvg python ${CLAUDE_SKILL_DIR}/scripts/ftb_print.py"
```

`collect` starts the `ftb` server as defined in the nearest `.mcp.json` (current directory
or above). If the server is configured elsewhere (user settings, another name), write a
one-server `.mcp.json`-style file and pass `--mcp-config PATH` (and `--server NAME`).

| command | what it does |
|---|---|
| `$FTBP collect ID --out DIR/tree.json --locale L [--depth N]` | Pulls the pedigree (max 15 generations) and every ancestor's facts and marriages, normalizes names/dates/places into the chart language, then prints the summary. |
| `$FTBP summary DIR/tree.json [--page A0] [--depth N]` | Ancestor count, generations, leaves, records to check, and a per-depth table with the font size the chart would print at on A1. |
| `$FTBP table DIR/tree.json [--depth N] [--flagged-only] [--out FILE]` | Markdown verification table in the chart language. |
| `$FTBP set DIR/tree.json AHN FIELD "VALUE"` | Fix one field. It applies to every occurrence of the same person, and the table then shows the edit with ✎ and the old value. |
| `$FTBP render DIR/tree.json --out DIR/chart.svg [--depth N]` | Writes the SVG plus a PDF next to it, and reports page orientation and the effective font size. |

Console output of the script is in English and meant for you. The table and the chart are
in the chart language. Everything you tell the user is in the conversation language.

Nodes are identified by **Ahnentafel number** (first table column): subject 1, father 2n,
mother 2n+1. So father = 2, mother = 3, father's father = 4, and so on. Editable fields:
`title first_name surname suffix gender birth_date birth_place death_date death_place
parents_marriage chart_title`. `parents_marriage` lives on the *child's* number (the
marriage of 2n and 2n+1 is stored on n). `chart_title` is global, so any AHN works.

Keep each person's files in `family-trees/<slug>/` under the current directory, where the
slug is the surname, first name and person id without diacritics: Caroline Bouvier
Kennedy, id 36 → `family-trees/kennedy-caroline-36/`.

## Workflow

### 0. Settle the language

Two languages matter: the **conversation language** (your questions, summaries and the
hand-over) and the **chart language** (the chart and the verification table). Both are the
language of the user's request. Use a different chart language only when the user asks
for one (e.g. writes in English, but the poster is for a grandmother in Prague).

If the request does not reveal a language (only a name and a date, mixed languages), ask
which language the user wants before anything else. Ask in the most likely language and
mention that any language works.

The chart language is a **locale**: `--locale en`, `cs` or `de` for the bundled files in
`${CLAUDE_SKILL_DIR}/locales/`. Use a bundled locale as it is unless the user asks for a
different format. For any other language (or regional variant, such as US
date order), copy `locales/en.json` to `family-trees/<slug>/locale-<code>.json`,
translate **every** value, and pass that path. A warning about missing keys means part of
the chart will print in English, so add the keys it names. Follow the conventions native
genealogists use:
- `date` and `month_year` patterns with `{d} {dd} {m} {mm} {month} {mon} {y}`
  (`{month}`/`{mon}` from `months`/`months_short`): `4 May 1882`, `4.5.1882`,
  `4 mai 1882`. If a full date needs a month form different from `month_year` (Polish
  `4 maja` vs `maj 1882`), use the numeric `{m}` in `date`;
- `born`/`died` marks: `b.`/`d.`, `n.`/`z.`, `*`/`†`, `°`/`†`;
- `qualifiers` for approximate dates (`abt.`, `kol.`, `um`, `vers`), and `between` ranges;
- `title` with `{name}`, table headers, field names, and review notes (keep each note's
  `{placeholders}`);
- `preferred_letters`: letters only this language uses (Polish `ąćęłńśźż`). With them the
  collector picks the native spelling from variants like `Andreas / Andrzej`.
  `preferred_given_names` can list native names without such letters.

The locale is baked in at `collect` (dates are formatted then). Switching the language later
means collecting again, which drops all `set` edits, so settle it first.

### 1. Identify the person (until you are certain)

Use the ftb MCP tools directly. Call `search_persons` with the name parts the user gave.
Narrow with `birth_year_from`/`birth_year_to` when a birth date is known. Matching
ignores case and diacritics and also covers married surnames.

- **Exactly one match** whose name *and* full birth date agree with what the user said:
  that is certain. Say whom you found (name, birth date and place, parents) and continue.
- **Exactly one match, but the user gave only a year or no date:** show who it is (birth
  date and place, parents) and ask for a quick confirmation before collecting.
- **Several matches or a weaker match** (only a year, only a name): show the candidates
  in a short list. For each, give birth date and place, parents and spouse
  (`get_person` with `include: ["facts","families"]`, or `get_relatives`). Ask which one
  it is, or ask for a detail that separates them (birthplace, a parent's name, spouse).
  Repeat until only one candidate fits.
- **No match**: try the first name or surname alone, a maiden or married name, or
  spelling variants. Then ask the user.

Never guess between two plausible people. A wrong root wastes the whole print.

### 2. Collect the ancestors

```bash
$FTBP collect <person_id> --out family-trees/<slug>/tree.json --locale <L> [--depth N]
```

Depth counts generations of *ancestors*: the person is generation 0, parents 1,
grandparents 2. "3 generations" therefore means up to the great-grandparents (`--depth 3`).

Always collect everything (omit `--depth`, the 15-generation maximum). Collection takes
seconds, and the summary then shows how much deeper the data goes. Apply the user's limit
later with `--depth` on `table` and `render`.

### 3. Report the summary and settle the depth

Relay the summary in the conversation language:
- total number of ancestors (and distinct people, if pedigree collapse made them differ),
- number of generations,
- number of leaves, i.e. ancestors without known parents. Explain briefly that leaves
  set how tall the chart gets: more leaves means a bigger chart and smaller print.

Also show the per-depth table from the summary output, with translated headers. The font
column is the real printed font size. It already includes the renderer's best effort: tight
spacing, plus wrapping places onto a second line when the page width is what limits the size.
Text below about 6 pt is hard to read on paper, so recommend the deepest depth that stays
readable (≥ 7 pt is comfortable). Then ask whether the user wants to limit or extend the depth.

If the depth the user wants prints below 7 pt, offer larger paper as the alternative to
cutting generations, and do so firmly below 6 pt. Paper size scales the text linearly: A0
gives about 1.41× the A1 size, and `summary --page A0 --depth N` shows the exact numbers.
Use the chosen page for `render --page …`.

- **Limit:** use `--depth N` for `table` and `render`. No re-collection is needed.
- **Extend beyond what was collected** (the summary says "the tree can be extended"):
  re-run `collect` with the larger `--depth` and report the new summary.

### 4. Verify and clean the data

The collector already applies the mechanical rules:
- dates follow the locale's patterns; approximate dates get its qualifiers
  (`abt. 1750`, `bef. 1750`, `aft. 1750`, `1750–1755`, `from …`, `to …` in English);
- places keep only the first comma segment, without house-number markers (`č.p.`, `Nr.`,
  `No.`), parentheses, and alias tails (`now …`, `nyní …`, `jetzt …`, ` - …`);
- of name variants (`Agnes / Anna`, `Wenzel (Václav)`, `Smith alias Smyth`) the form in
  the chart language wins: one with the locale's `preferred_letters` or on its
  `preferred_given_names` list. Otherwise the first variant wins;
- titles before the given name (`Ing.`, `Dr.`) and suffixes after the surname (`Sr`,
  `III`) are kept in their own fields;
- women are printed with their birth surname only;
- a missing birth falls back to baptism and a missing death to burial (both are flagged).

Every such change is listed in the table's last column together with the raw value.
A father's surname variant is chosen to match his child's surname, so a line keeps one
spelling.
Places that still look like two places (`/`), carry a description (`hospital`, `near …`,
`u …`, `bei …`) or are marked uncertain (`?`) are flagged too. A flag is a hint to look,
not an instruction to cut: a place can really be called "Bridge near the Mill". Keep a
`?` in the printed place: it is the source's way of saying the place is uncertain, and the
user wants that visible.

The script cannot judge meaning, so read **every** row yourself, not just the flagged
ones, and fix what the rules missed with `set`. The target place form is the settlement
plus a house number when the source has one (`Lhota 12`), or just the settlement, or just
a country. Look for:
- descriptions after the place name: `Springfield General Hospital` → `Springfield`,
  `Lhota u Příbramě` → `Lhota`;
- two places in one value (`Kilkenny / Callan`): pick one, and say so;
- place names in the chart language: use the established exonym (`Wien` → `Vienna` in
  English, `Vídeň` in Czech; `Ireland` → `Irland` in German). Leave places that have no
  established exonym as they are, and ask if you are unsure;
- the parentheses removed by the rules sometimes held the *current* name
  (`Pressburg (now Bratislava)`). Mention these so the user can choose;
- a married surname inside the given names (`Jacqueline Lee Kennedy` + `Bouvier`):
  propose removing it;
- a nickname in the given names (`John Francis "Honey Fitz"`): propose removing it;
- generational suffixes the chart language writes differently (`Sr`/`Jr` → `st.`/`ml.`
  in Czech, `sen.`/`jun.` in German): translate them with `set … suffix`. Keep roman
  numerals (`III`);
- dates still flagged as not recognized: convert them by hand into the locale's form if
  the meaning is clear, otherwise ask.

Make your own corrections with `set` *before* showing the table. The user then reviews
the result, and every change you made is visible in it (✎ with the old value).

Then show the table (`table`, with `--depth` if limited). With up to about 40 rows, paste
it into the chat. For larger trees, write the full table with
`--out family-trees/<slug>/table.md` and give the path, then paste `table --flagged-only`.
That output lists only rows with real findings or your edits; routine name-variant
picks are folded into one summary line at the end. Above the
table, list the corrections you made yourself so the user can veto them. Ask the user to
check names, dates and places and to send corrections.

Apply each correction with `set`. Show the affected rows again (`table --flagged-only`
lists flagged and edited rows) until the user confirms the data. Do not render before
that confirmation: the user asked to verify the data first, and a print run is expensive
to redo.

**Title.** The collector fills the locale's `title` template with the printed name in its
base form: *Ancestors of Caroline Bouvier Kennedy*, *Vorfahren von John Fitzgerald
Kennedy*. Languages with grammatical cases need the name declined: Czech *Rodina Johna
Fitzgeralda Kennedyho*, Polish *Rodzina Jana Kowalskiego*. Use the confirmed printed
name (after variant picks and corrections), not the raw one. Fix the title with
`set … 1 chart_title "…"` and show it to the user together with the table.

Shorter places make boxes narrower, which can raise the printed font, mostly at
shallower depths. For deep trees the height, i.e. the number of ancestors stacked in
one column, sets the size. If the user chose the depth with readability in mind, run
`summary` again after the corrections and mention any depth that has become readable.

### 5–6. Render

```bash
$FTBP render family-trees/<slug>/tree.json --out family-trees/<slug>/chart.svg [--depth N]
```

What the renderer handles:
- the person centered below the parents' boxes, which join on a horizontal bar labeled
  with their marriage date;
- the father's ancestors spreading to the right and the mother's to the left;
- father above mother in every older couple, with marriage dates rotated beside the
  vertical line;
- the title in a large decorative frame, placed in the empty space above the parents
  when it fits there (so it costs no height), otherwise above the chart;
- one A1 page in whichever orientation gives the larger text;
- when the page width is what limits the text size, places in the widest boxes wrap onto
  an indented second line, at whatever wrap width prints largest;
- uniform font and box styling.

Each ancestor box looks like this, with the locale's `born`/`died` marks (a line is
omitted when both its date and place are unknown; an unknown date with a known place
prints as `b. ?, Place`):
```
Given_name Surname Suffix     ← men
Given_name Birth_surname      ← women (birth surname only)
b. date, place
d. date, place
```

Options to offer only if relevant: `--no-title`, `--align center`
(default `left`), `--orientation landscape|portrait`, `--page A0…A4`.

Before handing the chart over, look at it yourself:
```bash
uv run -q --no-project --with cairosvg python -c "import cairosvg; cairosvg.svg2png(url='family-trees/<slug>/chart.svg', write_to='family-trees/<slug>/preview.png', output_width=3000)"
```
For charts with more than about 60 boxes a 3000 px overview is too coarse to judge text.
Render at `output_width=8000` and read a few crops (center, one outer column on each
side) with Pillow instead.
Read the PNG and check that boxes don't overlap, text stays inside boxes, dates are
readable and every visible word is in the chart language. If the reported font is under
6 pt, warn the user and suggest a smaller depth. If a small tree leaves most of the page
empty, offer a smaller format (`--page A2`/`A3`).

### 7. Hand over

Give the paths to the SVG and PDF and state the page size, orientation and printed
font size. If a file-sending tool is available (e.g. SendUserFile), send the PDF.
Offer adjustments: depth, title, alignment, data fixes. Fixes only need `set` + `render`,
with no new collection.

## Edge cases

- **Pedigree collapse:** a person reachable through two lines appears twice. The table
  shows the repeat as one short row pointing to the first occurrence, and the chart draws
  both boxes. That is correct for a pedigree, so mention it but don't "fix" it.
- **One parent unknown:** the known parent connects with a straight line and no
  marriage date.
- **Unknown gender:** a grey box, flagged in the table. Ask the user.
- **Living people** never get a death line.
- **Data problems you can't resolve** (e.g. impossible dates): ask. Never invent values.

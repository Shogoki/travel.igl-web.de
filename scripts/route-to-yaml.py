#!/usr/bin/env python3
"""Turn the hand-written list in content/unsere-route.md into route-data/route.yaml.

The list stays the source of truth: it is written for people, and the page
shows it as-is below the map. This script derives the map data from it:

1. Split the list into trips (one per `##` heading).
2. Send each changed trip to an OpenAI-compatible chat/completions endpoint,
   which returns its stops as JSON (name, country, dates, transport, rough
   coordinates and a geocoding query). The answer is validated here; on
   errors the model gets one retry with the error list.
3. A second request per trip assigns blog posts to stops, chosen from posts
   whose opening "dates – places" line overlaps the trip; posts whose lead
   line names a stop outright are linked without asking.
4. Geocode new places through OpenStreetMap Nominatim, using the model's
   rough coordinates to rule out namesakes (and in place of a result when
   there is none), and cache them in route-data/places.yaml. That file is
   hand-editable: correct a misplaced point there and rerun.
5. Write route-data/route.yaml with hashes of each trip's source text and
   candidate posts, so the next run only asks about what changed.

Both files live on the route-data branch, not on main, so CI can commit
them without anyone having to pull. route-data/ is that branch's checkout:
a git worktree locally (`git worktree add route-data route-data`), a second
checkout in CI. Hugo mounts route.yaml from there as data/route.yaml.

Standard library only, so CI needs no pip install. Configuration:

  LLM_BASE_URL  e.g. https://api.openai.com/v1 (the script appends
                /chat/completions)
  LLM_MODEL     e.g. gpt-4.1-mini, claude-haiku-4-5
  LLM_API_KEY   bearer token
  LLM_JSON_MODE json_schema (default) or none, for endpoints that reject
                response_format; the schema is then only in the prompt

A trip that fails (the model's answer stays invalid after the retry, the
endpoint is down) keeps its previous version, the others are written, and
the script exits 1; the next run retries only what failed. The site always
builds from the last good data.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "content", "unsere-route.md")
POSTS_DIR = os.path.join(ROOT, "content", "post")
DATA_DIR = os.path.join(ROOT, "route-data")
ROUTE_YAML = os.path.join(DATA_DIR, "route.yaml")
PLACES_YAML = os.path.join(DATA_DIR, "places.yaml")

TRANSPORT = ["flug", "bus", "zug", "auto", "boot", "fuss"]

USER_AGENT = "travel.igl-web.de route map generator (https://github.com/Shogoki/travel.igl-web.de)"


# ---------------------------------------------------------------------------
# Minimal YAML: this script writes both data files itself, so it only has to
# read back the subset it emits (block mappings, block lists, inline lists,
# JSON-quoted strings, numbers, null). Hand edits to places.yaml must keep
# that shape: `"query": {lat: 1.23, lon: 4.56}` per line.
# ---------------------------------------------------------------------------

def yaml_scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(v, ensure_ascii=False)


def parse_scalar(s):
    s = s.strip()
    if s in ("", "null", "~"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if s.startswith('"'):
        return json.loads(s)
    if s.startswith("'"):
        return s[1:-1].replace("''", "'")
    if s.startswith("[") or s.startswith("{"):
        return parse_flow(s)
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def parse_flow(s):
    # Flow collections as emitted below: keys are bare words or JSON strings.
    out = re.sub(r'([{,]\s*)([A-Za-z_][\w-]*)\s*:', r'\1"\2":', s)
    return json.loads(out)


def load_yaml(path):
    """Parse the block-style YAML subset this script writes."""
    if not os.path.exists(path):
        return None
    lines = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            lines.append(raw.rstrip("\n"))
    pos = 0

    def indent(line):
        return len(line) - len(line.lstrip(" "))

    def split_key(text):
        if text.startswith('"'):
            end = 1
            while True:
                end = text.index('"', end)
                if text[end - 1] != "\\":
                    break
                end += 1
            key = json.loads(text[: end + 1])
            rest = text[end + 1:]
            assert rest.startswith(":"), text
            return key, rest[1:].strip()
        key, _, rest = text.partition(":")
        return key.strip(), rest.strip()

    def block(level):
        nonlocal pos
        if lines[pos].lstrip().startswith("- "):
            items = []
            while pos < len(lines) and indent(lines[pos]) == level and lines[pos].lstrip().startswith("- "):
                text = lines[pos].lstrip()[2:]
                # A list item that starts a mapping: re-read it as a mapping
                # indented by two more columns.
                if re.match(r'^("[^"]*"|[\w-]+):(\s|$)', text):
                    lines[pos] = " " * (level + 2) + text
                    items.append(block(level + 2))
                else:
                    items.append(parse_scalar(text))
                    pos += 1
            return items
        mapping = {}
        while pos < len(lines) and indent(lines[pos]) == level and not lines[pos].lstrip().startswith("- "):
            key, rest = split_key(lines[pos].lstrip())
            pos += 1
            if rest == "":
                if pos < len(lines) and (indent(lines[pos]) > level or
                                         (indent(lines[pos]) == level and lines[pos].lstrip().startswith("- "))):
                    mapping[key] = block(indent(lines[pos]))
                else:
                    mapping[key] = None
            else:
                mapping[key] = parse_scalar(rest)
        return mapping

    return block(0) if lines else {}


# ---------------------------------------------------------------------------
# Source parsing
# ---------------------------------------------------------------------------

def read_source():
    with open(SOURCE, encoding="utf-8") as f:
        text = f.read()
    # Drop front matter.
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text


def split_trips(text):
    """Return [(heading, body)] for each `## ` section, top to bottom."""
    trips = []
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = [line[3:].strip(), []]
            trips.append(current)
        elif current is not None:
            # Shortcodes and blank lines carry no route information, and
            # leaving them out keeps such edits from costing a request.
            if "{{<" in line or "{{%" in line or not line.strip():
                continue
            current[1].append(line.rstrip())
    out = []
    for heading, body in trips:
        body_text = "\n".join(body).strip()
        out.append((heading, body_text))
    return out


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def trip_hash(heading, body):
    # The prompt and schema are part of it, so editing them redoes every trip.
    return digest(STOPS_PROMPT, STOPS_SCHEMA, heading, body)


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

def parse_front_matter(text):
    fm = {}
    if not text.startswith("---"):
        return fm, text
    _, head, body = text.split("---", 2)
    for line in head.splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return fm, body


def parse_lead_dates(lead):
    """Dates of a post's opening line, e.g. "11.-14.09.2022 – Lima"."""
    m = re.match(r"^([\d.\s\-–]+)", lead)
    if not m:
        return None
    nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
    try:
        if len(nums) == 3:                       # 08.12.2022
            d1, mo1, y = nums
            start = end = dt.date(y, mo1, d1)
        elif len(nums) == 4:                     # 11.-14.09.2022
            d1, d2, mo, y = nums
            start, end = dt.date(y, mo, d1), dt.date(y, mo, d2)
        elif len(nums) == 5:                     # 22.10.-25.10.2022
            d1, mo1, d2, mo2, y = nums
            start, end = dt.date(y, mo1, d1), dt.date(y, mo2, d2)
        elif len(nums) == 6:                     # 30.12.2022-01.01.2023
            d1, mo1, y1, d2, mo2, y2 = nums
            start, end = dt.date(y1, mo1, d1), dt.date(y2, mo2, d2)
        else:
            return None
    except ValueError:
        return None
    if start > end:                              # 11.09.-06.08.2023
        start = start.replace(year=start.year - 1)
    return start, end


def read_posts():
    posts = []
    for name in sorted(os.listdir(POSTS_DIR)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(POSTS_DIR, name), encoding="utf-8") as f:
            fm, body = parse_front_matter(f.read())
        lead = next((l[3:].strip() for l in body.splitlines() if l.startswith("## ")), "")
        dates = parse_lead_dates(lead)
        if not dates:
            continue
        posts.append({
            "slug": name[:-3],
            "title": fm.get("title", ""),
            "lead": lead,
            "from": dates[0],
            "to": dates[1],
        })
    return posts


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

STOPS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "stops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "query": {"type": "string"},
                    "country": {"type": "string"},
                    "country_code": {"type": "string"},
                    "from": {"type": "string"},
                    "to": {"type": ["string", "null"]},
                    "by": {"type": ["string", "null"], "enum": TRANSPORT + [None]},
                    "label": {"type": ["string", "null"]},
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                },
                "required": ["name", "query", "country", "country_code", "from", "to", "by", "label",
                             "lat", "lon"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "stops"],
    "additionalProperties": False,
}

STOPS_PROMPT = """You convert one section of a German travel blog's route list into JSON.

The section is one trip. Its heading may contain the trip name, dates and flags.
List lines are either stays ("12.09. – 14.09. Lima") or transport legs
("14.09. Nachtbus nach Huaraz", "Flug von Fortaleza nach Rio").

Return the trip's title (the heading without dates and flag emoji, e.g.
"Thailand (Internship)") and its stops in travel order:

- name: the place as written in the list, cleaned of dates, flags and notes
  in parentheses that are not part of the name ("Nauta, Iquitos (inklusive
  Dschungel-Tour)" -> two stops "Nauta" and "Iquitos"). Keep German spelling.
  A line naming two places that were visited one after another becomes two
  stops with the same dates. Keep repeated visits as separate stops.
- query: a geocoding query for OpenStreetMap Nominatim that finds this place,
  in English with the spelling OSM uses ("Ko Lanta", "Ko Samui"), ending in
  the country ("Huaraz, Peru", "Koh Phi Phi, Thailand",
  "Joshua Tree National Park, USA", "Java, Indonesia"). Where the name is
  ambiguous within the country, add the state or province in between
  ("Ilha Grande, Rio de Janeiro, Brazil"); pick the one that fits the route.
  Nothing else: no islands or regions ("Yogyakarta, Indonesia", not
  "Yogyakarta, Java, Indonesia"), no notes.
- lat / lon: the place's approximate coordinates in decimal degrees (the
  town centre, the island, the middle of a region).
- country: the country in German ("Kolumbien", "USA"); country_code: its
  ISO 3166-1 alpha-2 code in upper case.
- from / to: ISO dates (YYYY-MM-DD). Years are often missing: infer them from
  the heading, from the year given anywhere in the section, and from the hint
  below; dates run forward in time, and a year rolls over after December.
  A stop with no own stay line (only reached by a transport line, e.g.
  "03.11. Bus nach Buenaventura" followed by "04.11. Bus nach Armenia") gets
  from = to = that day. When the heading gives the only dates, the single stop
  uses them. "seit 07.11.26" (still there) means to = null.
- by: how this stop was reached, if the list says so: flug, bus (also
  Nachtbus, Collectivo, Chickenbus), zug (also Amtrak), auto (taxi, tuktuk,
  roadtrip, car, Weiterfahrt by road), boot (boat, ferry, Frachtschiff),
  fuss (on foot). null when the list does not say; never guess ("Visarun
  nach Nha Trang", "Weiterreise nach Java" don't say how).
- label: the whole transport line as written, minus its date and the flag
  emoji, keeping all words after them ("Flug von Frankfurt 🇩🇪 nach Lima 🇵🇪"
  -> "Flug von Frankfurt nach Lima", "Frachtschiff Yurimaguas - Nauta");
  null if none.
- A trip that starts with a flight from home ("Flug von Frankfurt nach Lima")
  starts with a stop for the origin (Frankfurt, by null) dated on the
  departure day, and a final flight home ends with a stop for home.
- Transcribe; do not correct, merge or invent dates or places.

Answer with the JSON object only."""

POSTS_SCHEMA = {
    "type": "object",
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stop": {"type": "integer"},
                    "name": {"type": "string"},
                    "posts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["stop", "name", "posts"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["links"],
    "additionalProperties": False,
}

POSTS_PROMPT = """You link blog posts to the stops of a trip.

Each post starts with a line "dates – places" saying which days and places it
covers. Link a post to every stop whose place it covers (also when it names
the region or a different spelling: "Jervis Bay Region" covers Shoalhaven and
Mollymook; "Tokio" is Tokyo) and whose dates overlap the post's dates.
Do not link overview posts that summarise a whole country or the whole trip
("Peru", "Kolumbien", "Einmal um die Welt") or posts about places that are
not stops. A post may be linked to several stops; a stop may have several
posts or none.

Answer with JSON: {"links": [{"stop": <stop index>, "name": <that stop's
name, exactly as listed>, "posts": [<slug>, ...]}]}, listing only stops that
get at least one post. Check each post against every stop in its date range;
a post naming three places links to three stops."""


class LLMError(Exception):
    pass


def chat(messages, schema, name):
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    body = {
        "model": os.environ["LLM_MODEL"],
        "messages": messages,
        "max_completion_tokens": 32000,
    }
    if os.environ.get("LLM_JSON_MODE", "json_schema") == "json_schema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        }
    else:
        messages = [dict(m) for m in messages]
        messages[0]["content"] += "\n\nJSON schema of the answer:\n" + json.dumps(schema)
        body["messages"] = messages
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["LLM_API_KEY"],
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            if e.code in (429, 500, 502, 503, 529) and attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            raise LLMError(f"HTTP {e.code}: {detail}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            raise LLMError(str(e))
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("unexpected response: " + json.dumps(data)[:500])
    if choice.get("finish_reason") == "length":
        raise LLMError("answer was cut off (finish_reason=length)")
    return content


def parse_json_answer(content):
    text = content.strip()
    # Some endpoints wrap the JSON in a Markdown code fence.
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text)


def ask(system, user, schema, name, validate):
    """One request plus at most one repair retry; returns validated data."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        content = chat(messages, schema, name)
        try:
            data = parse_json_answer(content)
            errors = validate(data)
        except (ValueError, TypeError, KeyError) as e:
            errors = [f"invalid JSON: {e}"]
        if not errors:
            return data
        if attempt == 0:
            print(f"    retrying, {len(errors)} problem(s): {errors[:3]}", file=sys.stderr)
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": "Your answer has these problems; send the corrected full JSON:\n- "
                 + "\n- ".join(errors[:30])},
            ]
    raise LLMError(f"{name}: still invalid after retry: {errors[:5]}")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def iso(s):
    return dt.date.fromisoformat(s)


FULL_DATE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?![\d])")


def explicit_dates(text):
    """Dates written with a year in the list ("06.08.2023", "30.08.26")."""
    out = set()
    for d, m, y in FULL_DATE.findall(text):
        y = int(y) + (2000 if len(y) == 2 else 0)
        try:
            out.add(dt.date(y, int(m), int(d)).isoformat())
        except ValueError:
            pass
    return sorted(out)


def validate_stops(data, open_ended_ok, anchors=()):
    errors = []
    stops = data.get("stops")
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        errors.append("title is empty")
    if not isinstance(stops, list) or not stops:
        return errors + ["stops is empty"]
    prev = None
    for i, s in enumerate(stops):
        where = f"stop {i} ({s.get('name')})"
        for key in ("name", "query", "country"):
            if not isinstance(s.get(key), str) or not s[key].strip():
                errors.append(f"{where}: {key} is empty")
        if not re.fullmatch(r"[A-Z]{2}", s.get("country_code") or ""):
            errors.append(f"{where}: country_code must be two upper-case letters")
        if not (isinstance(s.get("lat"), (int, float)) and isinstance(s.get("lon"), (int, float))
                and -90 <= s["lat"] <= 90 and -180 <= s["lon"] <= 180):
            errors.append(f"{where}: lat/lon missing or out of range")
        if s.get("by") not in TRANSPORT + [None]:
            errors.append(f"{where}: by must be one of {TRANSPORT} or null")
        try:
            start = iso(s["from"])
        except (ValueError, TypeError, KeyError):
            errors.append(f"{where}: from is not an ISO date")
            continue
        if s.get("to") is None:
            if not (open_ended_ok and i == len(stops) - 1):
                errors.append(f"{where}: to is null, but only the current, last stop may be open-ended")
        else:
            try:
                end = iso(s["to"])
                if end < start:
                    errors.append(f"{where}: to is before from")
                elif (end - start).days > 200:
                    errors.append(f"{where}: stay of {(end - start).days} days, check the year")
            except (ValueError, TypeError):
                errors.append(f"{where}: to is not an ISO date")
        if prev and start < prev:
            errors.append(f"{where}: starts before the previous stop, check order and year")
        prev = start
    # Most dates in the list have no year, so a whole trip can be shifted by
    # one and still look consistent. The dates that do carry a year pin it:
    # each must fall within the trip (a heading may name a planned end, so
    # it needn't be a stop's own date).
    try:
        first = min(iso(s["from"]) for s in stops)
        last = max(iso(s["to"]) if s.get("to") else dt.date.max for s in stops)
    except (ValueError, TypeError, KeyError):
        return errors
    slack = dt.timedelta(days=1)
    upper = last if last == dt.date.max else last + slack
    for a in anchors:
        if not first - slack <= iso(a) <= upper:
            errors.append(f"the list gives the full date {a}, outside the stops' dates "
                          f"({first} .. {'' if last == dt.date.max else last}); check the years of all stops")
    return errors


def same_name(answer, name):
    """Whether the model's echo of a stop name is that stop ("Lima (Peru)")."""
    if not isinstance(answer, str):
        return False
    norm = lambda x: re.sub(r"\s*[(|].*$", "", x).strip().casefold()
    return norm(answer) == norm(name)


def fold(text):
    """Lower case without accents: "Medellín" -> "medellin"."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def literal_links(stops, posts):
    """Links any small model can miss: the post's lead line names the stop.

    "26.-28.02.2023 – Lampung, Danau Ranau, Lahat" covers the stops Ranau
    and Lahat, whatever the model made of it.
    """
    links = {}
    for j, s in enumerate(stops):
        name = fold(re.sub(r"\s*\(.*?\)", "", s["name"])).strip()
        s_from = iso(s["from"])
        s_to = iso(s["to"]) if s.get("to") else dt.date.max
        for p in posts:
            if p["from"] <= s_to and p["to"] >= s_from and \
                    re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", fold(p["lead"])):
                links.setdefault(j, []).append(p["slug"])
    return links


def validate_links(data, n_stops, stops, posts_by_slug):
    errors = []
    for link in data.get("links", []):
        i = link.get("stop")
        if not isinstance(i, int) or not 0 <= i < n_stops:
            errors.append(f"stop index {i} does not exist")
            continue
        stop = stops[i]
        # Small models slip by one in a long list; the name catches it.
        if not same_name(link.get("name"), stop["name"]):
            errors.append(f"stop {i} is {stop['name']!r}, not {link.get('name')!r}; check the indices")
            continue
        s_from = iso(stop["from"])
        s_to = iso(stop["to"]) if stop.get("to") else dt.date.max
        for slug in link.get("posts", []):
            post = posts_by_slug.get(slug)
            if not post:
                errors.append(f"post {slug!r} is not in the list")
                continue
            # A day of slack: lists and posts disagree by one day at times.
            if post["to"] + dt.timedelta(days=1) < s_from or post["from"] - dt.timedelta(days=1) > s_to:
                errors.append(f"post {slug} ({post['lead']}) does not overlap stop {i} {stop['name']} "
                              f"{stop['from']}..{stop.get('to')}")
    return errors


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

_last_geocode = [0.0]


def geocode(query, country_code):
    """Nominatim's candidates for a query that are places, best-ranked first.

    Free-text search also matches businesses and streets named after a
    place ("Koh Lanta" is a restaurant in Bangkok); only settlements,
    islands, regions and natural features count.
    """
    # Nominatim's usage policy: at most one request per second, with an
    # identifying User-Agent.
    wait = 1.1 - (time.time() - _last_geocode[0])
    if wait > 0:
        time.sleep(wait)
    params = {"q": query, "format": "jsonv2", "limit": "10", "accept-language": "de"}
    if country_code:
        params["countrycodes"] = country_code.lower()
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            results = json.load(resp)
    finally:
        _last_geocode[0] = time.time()
    return [{"lat": round(float(r["lat"]), 4), "lon": round(float(r["lon"]), 4),
             "region": r.get("addresstype") in REGION_TYPES}
            for r in results if r.get("category") in PLACE_CATEGORIES]


# Cities are often only an administrative boundary in OSM, so those count.
PLACE_CATEGORIES = {"place", "boundary", "natural", "water", "leisure"}
# ...but a province named like its capital (Cajamarca, Battambang, Kratié)
# ranks above the town, and its centroid can be 100 km away.
REGION_TYPES = {"state", "province", "region", "state_district", "county", "district"}

# How far Nominatim's answer may be from the model's estimate and still be
# taken as the same place. Small models are often 100 km or more off, but
# namesakes are usually much further apart.
MATCH_KM = 300


def query_variants(query):
    """The query, then simpler spellings OSM is likelier to know."""
    out = [query]
    parts = [p.strip() for p in query.split(",")]
    if len(parts) > 2:                      # "Medan, Sumatra, Indonesia"
        out.append(f"{parts[0]}, {parts[-1]}")
    if re.match(r"(?i)koh ", query):        # OSM writes Thai islands "Ko ..."
        out += [re.sub(r"(?i)^koh ", "Ko ", q) for q in list(out)]
    return out


def locate(query, country_code, estimate, previous=None):
    """Coordinates for a place: the model's estimate, refined by Nominatim.

    Nominatim alone picks namesakes: "Koh Lanta, Thailand" finds a Bangkok
    restaurant, "Yogyakarta, Java" a street in Surabaya, "Santa Rosa, Peru"
    the wrong one of several. The model knows roughly where the place is, so
    a candidate must lie near its estimate, or near the previous stop (the
    estimate itself can be badly off: one put Medan on Kuala Lumpur). With
    no candidate, the estimate stands in, logged for a look at
    route-data/places.yaml.
    """
    refs = [estimate] + ([previous] if previous else [])
    for q in query_variants(query):
        try:
            cands = geocode(q, country_code)
        except (urllib.error.URLError, OSError) as e:
            print(f"  Nominatim failed for {q!r} ({e})", file=sys.stderr)
            break
        # Nominatim's ranking beats closeness to a rough estimate: Vancouver
        # city outranks Vancouver Island, Cartagena de Indias a hamlet in
        # Cesar. A town beats a province of the same name.
        near = [c for c in cands if any(distance_km(c, ref) <= MATCH_KM for ref in refs)]
        near = [c for c in near if not c["region"]] or near
        if near:
            return {"lat": near[0]["lat"], "lon": near[0]["lon"]}
    print(f"  warning: no Nominatim match near the model's estimate for {query!r}; using the estimate, "
          "check it in route-data/places.yaml", file=sys.stderr)
    return {"lat": round(float(estimate["lat"]), 4), "lon": round(float(estimate["lon"]), 4)}


def distance_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


# Longest plausible leg per transport type, for spotting misplaced points.
MAX_LEG_KM = {"flug": 20000, "bus": 1500, "zug": 1500, "auto": 2500, "boot": 1500, "fuss": 50, None: 1500}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def flag(cc):
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc.upper())


def slugify(s):
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def write_route(trips):
    out = [
        "# Generated by scripts/route-to-yaml.py from content/unsere-route.md.",
        "# Edit the list there, not this file: it is overwritten on the next run.",
        "trips:",
    ]
    for t in trips:
        out.append(f"  - id: {yaml_scalar(t['id'])}")
        for key in ("title", "heading", "hash", "posts_hash"):
            out.append(f"    {key}: {yaml_scalar(t[key])}")
        out.append("    stops:")
        for s in t["stops"]:
            first = True
            for key in ("name", "country", "country_code", "flag", "lat", "lon", "from", "to", "by", "label",
                        "query", "posts"):
                v = s.get(key)
                if key == "posts":
                    v = "[" + ", ".join(yaml_scalar(p) for p in (v or [])) + "]"
                else:
                    v = yaml_scalar(v)
                out.append(("      - " if first else "        ") + f"{key}: {v}")
                first = False
    tmp = ROUTE_YAML + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, ROUTE_YAML)


def write_places(places):
    out = [
        "# Geocoding cache for scripts/route-to-yaml.py (OpenStreetMap Nominatim).",
        "# If a stop sits in the wrong spot, correct its lat/lon here: the next run",
        "# copies it into route.yaml. Entries are never refetched; delete one to",
        "# fetch it again.",
    ]
    for q in sorted(places):
        p = places[q]
        out.append(f"{yaml_scalar(q)}: {{lat: {p['lat']!r}, lon: {p['lon']!r}}}")
    tmp = PLACES_YAML + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, PLACES_YAML)


# ---------------------------------------------------------------------------

def build_trips(sections, old_trips, places, posts_hash, trip_range, trips, redo):
    """Fill `trips`; return the headings of trips that failed.

    A failed trip keeps its previous version (matched by heading) if there
    is one, so a flaky request costs neither the other trips' answers nor
    the map; the next run retries it because its hash still differs.
    """
    by_heading = {t.get("heading"): t for t in old_trips.values()}
    failed = []
    for i, (heading, body) in enumerate(sections):
        try:
            trips.append(build_trip(i, heading, body, old_trips, places, posts_hash, trip_range, redo(heading)))
        except (LLMError, urllib.error.URLError, OSError) as e:
            print(f"error: trip {heading!r}: {e}", file=sys.stderr)
            failed.append(heading)
            if heading in by_heading:
                print("  keeping its previous version", file=sys.stderr)
                trips.append(by_heading[heading])
    return failed


def ask_links(stops, cand):
    """{stop index: [post slugs]} as the model sees it."""
    stop_lines = "\n".join(f"{j}: {s['name']} | {s['country']} | {s['from']} .. {s.get('to') or 'heute'}"
                           for j, s in enumerate(stops))
    post_lines = "\n".join(f"{p['slug']}: {p['lead']}" for p in cand)
    data = ask(
        POSTS_PROMPT,
        f"Stops:\n{stop_lines}\n\nPosts:\n{post_lines}",
        POSTS_SCHEMA, "post_links",
        lambda d: validate_links(d, len(stops), stops, {p["slug"]: p for p in cand}),
    )
    links = {}
    for link in data["links"]:
        links.setdefault(link["stop"], []).extend(link["posts"])
    return links


def build_trip(i, heading, body, old_trips, places, posts_hash, trip_range, force):
    # The top section is the current trip; only its last stop may lack an end.
    h = trip_hash(heading, body)
    prev = None if force else old_trips.get(h)
    if prev:
        # Their coordinates serve as the estimate if a place drops out of
        # the cache.
        stops = [{k: v for k, v in s.items() if k not in ("posts", "flag")} for s in prev["stops"]]
        title = prev["title"]
    else:
        print(f"trip {heading!r}: reading stops", file=sys.stderr)
        anchors = explicit_dates(heading + "\n" + body)
        hints = ["this is the newest trip; today is " + dt.date.today().isoformat()
                 + "; an end date not yet known (\"xx.11.26\", \"?\") means to = null" if i == 0
                 else "this trip is over, every stop has an end date"]
        if anchors:
            hints.append("dates with a year in this section: " + ", ".join(anchors)
                         + " (all lie within the trip)")
        data = ask(
            STOPS_PROMPT,
            f"Heading: {heading}\n\nSection:\n{body or '(no list lines)'}\n\n"
            f"Hints: {'; '.join(hints)}.",
            STOPS_SCHEMA, "trip_stops",
            lambda d: validate_stops(d, open_ended_ok=(i == 0), anchors=anchors),
        )
        stops, title = data["stops"], data["title"].strip()
        for s in stops:
            s["country_code"] = s["country_code"].upper()

    cand, ph = posts_hash(*trip_range(stops))
    if prev and not force and ph == prev.get("posts_hash"):
        links = {j: s.get("posts") or [] for j, s in enumerate(prev["stops"])}
    elif cand:
        print(f"trip {title!r}: linking {len(cand)} candidate posts", file=sys.stderr)
        links = ask_links(stops, cand)
    else:
        links = {}
    for j, slugs in literal_links(stops, cand).items():
        links.setdefault(j, []).extend(slugs)
    # Unique, in post order (the posts are sorted by file name).
    order = {p["slug"]: k for k, p in enumerate(cand)}
    links = {j: sorted(set(v), key=lambda x: order.get(x, len(order))) for j, v in links.items()}

    for j, s in enumerate(stops):
        key = s["query"]
        if key not in places:
            print(f"  geocoding {key!r}", file=sys.stderr)
            # After a flight the previous stop says nothing about this one.
            previous = stops[j - 1] if j and s.get("by") != "flug" else None
            places[key] = locate(key, s.get("country_code"), s, previous)
        s["lat"], s["lon"] = places[key]["lat"], places[key]["lon"]
        s["flag"] = flag(s["country_code"])
        s["posts"] = links.get(j, [])
        s.setdefault("label", None)

    year = iso(stops[0]["from"]).year
    return {
        "id": f"{slugify(title)}-{year}",
        "title": title,
        "heading": heading,
        "hash": h,
        "posts_hash": ph,
        "stops": stops,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true", help="regenerate every trip")
    ap.add_argument("--redo", action="append", default=[], metavar="TEXT",
                    help="regenerate trips whose heading contains TEXT (repeatable)")
    ap.add_argument("--check", action="store_true",
                    help="only report whether route-data/route.yaml is out of date (exit 1 if so)")
    args = ap.parse_args()
    if not os.path.isdir(DATA_DIR):
        print("route-data/ is missing: it is the route-data branch, checked out with\n"
              "  git worktree add route-data route-data", file=sys.stderr)
        return 1

    def redo(heading):
        return args.force or any(text in heading for text in args.redo)

    sections = split_trips(read_source())
    posts = read_posts()
    old = load_yaml(ROUTE_YAML) or {}
    old_trips = {t["hash"]: t for t in old.get("trips") or []}
    places = load_yaml(PLACES_YAML) or {}

    def posts_hash(trip_from, trip_to):
        cand = [p for p in posts if p["to"] >= trip_from - dt.timedelta(days=1)
                and p["from"] <= trip_to + dt.timedelta(days=1)]
        return cand, digest(POSTS_PROMPT, POSTS_SCHEMA, [[p["slug"], p["lead"]] for p in cand])

    def trip_range(stops):
        start = iso(stops[0]["from"])
        end = max(iso(s["to"]) if s.get("to") else dt.date.today() for s in stops)
        return start, end

    # Which trips are stale?
    stale = []
    for i, (heading, body) in enumerate(sections):
        h = trip_hash(heading, body)
        prev = old_trips.get(h)
        if redo(heading) or not prev:
            stale.append(heading)
            continue
        _, ph = posts_hash(*trip_range(prev["stops"]))
        if ph != prev.get("posts_hash"):
            stale.append(heading + " (posts)")
        elif any(not set(slugs) <= set(prev["stops"][j].get("posts") or [])
                 for j, slugs in literal_links(prev["stops"], posts).items()):
            stale.append(heading + " (links)")
        elif any(s["query"] not in places or (s["lat"], s["lon"]) != (places[s["query"]]["lat"],
                                                                     places[s["query"]]["lon"])
                 for s in prev["stops"]):
            # A corrected entry in places.yaml is copied over; a deleted one
            # is fetched again.
            stale.append(heading + " (places)")
    if args.check:
        for s in stale:
            print("stale:", s)
        return 1 if stale or len(old_trips) != len(sections) else 0
    if not stale and len(old_trips) == len(sections):
        print("route-data/route.yaml is up to date")
        return 0

    # Copying places and literal post links needs no model.
    needs_llm = any(not h.endswith((" (places)", " (links)")) for h in stale) or len(old_trips) != len(sections)
    for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        if needs_llm and not os.environ.get(key):
            print(f"{key} is not set", file=sys.stderr)
            return 2

    trips = []
    try:
        failed = build_trips(sections, old_trips, places, posts_hash, trip_range, trips, redo)
    finally:
        # Keep what was geocoded even if a later trip failed.
        write_places(places)

    # Two trips with the same title in one year would collide.
    ids = [t["id"] for t in trips]
    for t in trips:
        if ids.count(t["id"]) > 1:
            t["id"] += "-" + t["hash"][:4]

    for t in trips:
        for a, b in zip(t["stops"], t["stops"][1:]):
            d = distance_km(a, b)
            if d > MAX_LEG_KM.get(b.get("by"), 1500):
                print(f"warning: {t['title']}: {a['name']} -> {b['name']} is {d:.0f} km "
                      f"by {b.get('by') or 'unknown'}; check route-data/places.yaml", file=sys.stderr)

    # Drop cache entries no stop uses any more (renamed queries).
    used = {s["query"] for t in trips for s in t["stops"]}
    write_places({q: v for q, v in places.items() if q in used})
    write_route(trips)
    print(f"wrote route-data/route.yaml ({sum(len(t['stops']) for t in trips)} stops in {len(trips)} trips)")
    if failed:
        print(f"{len(failed)} trip(s) failed and kept their previous version (if any): {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (LLMError, urllib.error.URLError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        print("route-data/route.yaml was not changed.", file=sys.stderr)
        sys.exit(1)

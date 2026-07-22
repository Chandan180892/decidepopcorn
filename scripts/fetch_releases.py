#!/usr/bin/env python3
"""Release Radar — auto-refresh fetcher.

Merges three sources into data/releases.json + docs/data.js:

1. data/curated.json  — hand-researched entries (media-scout agent / human).
                        Always wins on title collisions.
2. TVMaze API         — keyless. Streaming ("web") + TV schedules for the
                        next N days, US + IN + global, filtered to premieres
                        and notable shows so the board stays clean.
3. TMDB API           — optional but strongly recommended. Set TMDB_API_KEY
                        to auto-discover movies AND series worldwide with
                        posters, trailers, cast and ratings. Sweeps region
                        IN across every major Indian language (Hindi, Tamil,
                        Telugu, Malayalam, Kannada, Bengali, Marathi,
                        Punjabi) plus US/GB, and TV across those languages,
                        over a long forward window so upcoming tentpoles
                        appear in "Coming Soon". This is the backbone of
                        near-complete coverage — without it the board relies
                        only on the curated list + TVMaze (series only).

Run:  python3 scripts/fetch_releases.py
Env:  TMDB_API_KEY (optional)  DAYS_AHEAD (14)  DAYS_BACK (21)
      TMDB_DAYS_AHEAD (120)
"""

import gzip
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURATED = os.path.join(ROOT, "data", "curated.json")
OUT_JSON = os.path.join(ROOT, "data", "releases.json")
OUT_JS = os.path.join(ROOT, "docs", "data.js")

DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "14"))
DAYS_BACK = int(os.environ.get("DAYS_BACK", "21"))
TMDB_DAYS_AHEAD = int(os.environ.get("TMDB_DAYS_AHEAD", "120"))
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()

MAJOR_STREAMERS = {
    "netflix", "hbo max", "max", "prime video", "amazon prime video", "apple tv+",
    "disney+", "hulu", "paramount+", "peacock", "jiohotstar", "hotstar",
    "disney+ hotstar", "zee5", "sonyliv", "sun nxt", "aha", "mx player",
    "amazon mx player", "crunchyroll", "lionsgate play",
}
INDIA_STREAMERS = {
    "jiohotstar", "hotstar", "disney+ hotstar", "zee5", "sonyliv", "sun nxt",
    "aha", "mx player", "amazon mx player",
}


def http_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "release-radar/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def norm_title(title):
    t = re.sub(r"\(.*?\)", "", title.lower())
    t = re.sub(r"season\s*\d+|s\d+\b", "", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def yt_search(title):
    q = urllib.parse.quote_plus(f"{title} official trailer")
    return f"https://www.youtube.com/results?search_query={q}"


# ---------------------------------------------------------------- TVMaze ----

def fetch_tvmaze():
    """Season/series premieres and notable episodes across web + TV schedules."""
    items, seen_shows = [], set()
    today = date.today()
    days = [today + timedelta(days=d) for d in range(-DAYS_BACK, DAYS_AHEAD + 1)]
    urls = []
    for d in days:
        urls.append(f"https://api.tvmaze.com/schedule/web?date={d.isoformat()}")
        for cc in ("US", "IN"):
            urls.append(f"https://api.tvmaze.com/schedule?country={cc}&date={d.isoformat()}")

    season_cache = {}

    def season_episodes(show_id, season_no):
        """Episode count for a season via /shows/:id/seasons (episodeOrder)."""
        if show_id not in season_cache:
            try:
                season_cache[show_id] = http_json(f"https://api.tvmaze.com/shows/{show_id}/seasons")
            except Exception:
                season_cache[show_id] = []
        for s in season_cache[show_id]:
            if s.get("number") == season_no:
                return s.get("episodeOrder")
        return None

    for url in urls:
        try:
            eps = http_json(url)
        except Exception as e:
            print(f"  ! tvmaze fetch failed ({url}): {e}", file=sys.stderr)
            continue
        for ep in eps:
            show = (ep.get("_embedded") or {}).get("show") or ep.get("show") or {}
            if not show or show.get("id") in seen_shows:
                continue
            network = (show.get("webChannel") or show.get("network") or {}) or {}
            net_name = (network.get("name") or "").strip()
            country = (network.get("country") or {}) or {}
            rating = (show.get("rating") or {}).get("average")
            is_premiere = ep.get("number") == 1
            is_major = net_name.lower() in MAJOR_STREAMERS
            is_notable = (rating or 0) >= 7.5
            lang = show.get("language") or ""
            if not (is_major and (is_premiere or is_notable)):
                continue
            if not show.get("image"):
                continue
            seen_shows.add(show.get("id"))

            audience = ["worldwide"]
            if net_name.lower() in INDIA_STREAMERS or country.get("code") == "IN" or lang in (
                "Hindi", "Tamil", "Telugu", "Malayalam", "Kannada", "Bengali", "Marathi"
            ):
                audience = ["india"]
            elif net_name.lower() in {"netflix", "prime video", "amazon prime video",
                                      "apple tv+", "hbo max", "max", "disney+"}:
                audience = ["worldwide", "india"]

            season = ep.get("season")
            title = show.get("name", "?")
            if season and season > 1 and is_premiere:
                title = f"{title} (Season {season})"
            items.append({
                "id": f"tvmaze-{show.get('id')}",
                "title": title,
                "type": "series",
                "medium": "ott",
                "audience": audience,
                "platform": net_name or "TV",
                "date": ep.get("airdate"),
                "language": lang,
                "country": country.get("name") or "",
                "genres": show.get("genres") or [],
                "season": season,
                "episodes": season_episodes(show.get("id"), season) if is_premiere else None,
                "epRuntime": show.get("averageRuntime"),
                "cast": [],
                "synopsis": strip_html(show.get("summary"))[:280],
                "poster": ((show.get("image") or {}).get("original")
                           or (show.get("image") or {}).get("medium")),
                "trailer": yt_search(show.get("name", "")),
                "rating": f"{rating}/10 TVMaze" if rating else None,
                "source": "tvmaze",
            })
    print(f"  tvmaze: {len(items)} items")
    return items


# ------------------------------------------------------------------ TMDB ----

TMDB_LANG_NAME = {
    "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam",
    "kn": "Kannada", "bn": "Bengali", "mr": "Marathi", "pa": "Punjabi",
    "en": "English", "ko": "Korean", "ja": "Japanese", "es": "Spanish",
    "fr": "French", "de": "German", "it": "Italian", "zh": "Chinese",
}
INDIA_LANGS = ["hi", "ta", "te", "ml", "kn", "bn", "mr", "pa"]


def tmdb(path, **params):
    params["api_key"] = TMDB_KEY
    url = f"https://api.themoviedb.org/3{path}?{urllib.parse.urlencode(params)}"
    return http_json(url)


def _tmdb_genre_map():
    gmap = {}
    for kind in ("movie", "tv"):
        try:
            for g in tmdb(f"/genre/{kind}/list").get("genres", []):
                gmap[g["id"]] = g["name"]
        except Exception as e:
            print(f"  ! tmdb {kind} genres failed: {e}", file=sys.stderr)
    return gmap


def fetch_tmdb():
    """Comprehensive movie + TV discovery across India's languages and the
    major worldwide regions, over a long forward window."""
    if not TMDB_KEY:
        print("  tmdb: no TMDB_API_KEY set — skipping (curated + tvmaze only). "
              "Add a free key for near-complete auto coverage.")
        return []

    today = date.today()
    lo = (today - timedelta(days=DAYS_BACK)).isoformat()
    hi = (today + timedelta(days=TMDB_DAYS_AHEAD)).isoformat()
    gmap = _tmdb_genre_map()

    # Build the query matrix. Each entry: (kind, audience, extra discover params).
    mv_dates = {"primary_release_date.gte": lo, "primary_release_date.lte": hi,
                "with_release_type": "2|3|4"}
    tv_dates = {"first_air_date.gte": lo, "first_air_date.lte": hi}
    queries = []
    # India movies: broad region sweep + one pass per language for regional films
    queries.append(("movie", ["india"], dict(region="IN", **mv_dates)))
    for lang in INDIA_LANGS:
        queries.append(("movie", ["india"], dict(with_original_language=lang, **mv_dates)))
    # Worldwide movies
    for region in ("US", "GB"):
        queries.append(("movie", ["worldwide"], dict(region=region, **mv_dates)))
    # Series: India languages + global English
    for lang in INDIA_LANGS:
        queries.append(("tv", ["india"], dict(with_original_language=lang, **tv_dates)))
    queries.append(("tv", ["worldwide"], dict(with_original_language="en", **tv_dates)))

    # Discover, deduping by (kind, id) and unioning the audiences that found it.
    raw = {}
    for kind, audience, params in queries:
        try:
            data = tmdb(f"/discover/{kind}", sort_by="popularity.desc", page=1, **params)
        except Exception as e:
            print(f"  ! tmdb discover {kind} {params} failed: {e}", file=sys.stderr)
            continue
        for m in data.get("results", [])[:20]:
            if not m.get("poster_path") and (m.get("vote_count") or 0) == 0:
                continue  # skip empty stubs with no art and no votes
            key = (kind, m["id"])
            if key in raw:
                for a in audience:
                    if a not in raw[key][1]:
                        raw[key][1].append(a)
            else:
                raw[key] = (m, list(audience))

    items = []
    for (kind, mid), (m, audience) in raw.items():
        cast, trailer = [], None
        try:
            cast = [c["name"] for c in tmdb(f"/{kind}/{mid}/credits").get("cast", [])[:6]]
            vids = tmdb(f"/{kind}/{mid}/videos").get("results", [])
            yt = next((v for v in vids if v.get("site") == "YouTube"
                       and v.get("type") == "Trailer"), None)
            if yt:
                trailer = f"https://www.youtube.com/watch?v={yt['key']}"
        except Exception:
            pass
        title = m.get("title") or m.get("name") or "?"
        lang = (m.get("original_language") or "").lower()
        items.append({
            "id": f"tmdb-{kind}-{mid}",
            "title": title,
            "type": "movie" if kind == "movie" else "series",
            "medium": "theatrical" if kind == "movie" else "ott",
            "audience": audience,
            "platform": "In Theatres" if kind == "movie" else "Streaming",
            "date": m.get("release_date") or m.get("first_air_date") or None,
            "language": TMDB_LANG_NAME.get(lang, lang.upper()),
            "country": ", ".join(m.get("origin_country", [])) or "",
            "genres": [gmap[g] for g in m.get("genre_ids", []) if g in gmap],
            "cast": cast,
            "synopsis": (m.get("overview") or "")[:280],
            "poster": f"https://image.tmdb.org/t/p/w342{m['poster_path']}" if m.get("poster_path") else None,
            "trailer": trailer or yt_search(title),
            "rating": f"{m['vote_average']:.1f}/10 TMDB" if m.get("vote_average") else None,
            "source": "tmdb",
        })
    print(f"  tmdb: {len(items)} unique items from {len(queries)} queries")
    return items


# ------------------------------------------------------------------ IMDb ----

IMDB_CACHE = os.path.join(ROOT, "data", "imdb_ids.json")


POSSESSIVE_PREFIX = re.compile(r"^[A-Z][\w.]*(?:\s+[A-Z][\w.]*)*(?:'s|s')\s+")


def _imdb_suggest(q):
    """Raw call to the keyless suggestion API for one query string."""
    if not q:
        return []
    url = ("https://v2.sg.media-imdb.com/suggestion/"
           f"{urllib.parse.quote(q[0].lower())}/{urllib.parse.quote(q.lower())}.json")
    try:
        return http_json(url).get("d", [])
    except Exception:
        return []


# The suggestion API tags each hit with a media-type qid -- by far the
# strongest disambiguation signal available (stronger than year: a
# generic one-word title like "Ruthless" matches a 1948 film, a 2020 TV
# series, AND an unrelated 2026 film, all under the exact same string).
QID_FOR_TYPE = {"movie": {"movie", "tvMovie", "short", "video"},
                "series": {"tvSeries", "tvMiniSeries", "tvSpecial"}}


def imdb_lookup_id(title, item_type=None, year_hint=None, allow_older=False):
    """Resolve a title to an IMDb tconst via the keyless suggestion API.

    Only ever returns an EXACT normalized-title match. A previous version
    also accepted a same-prefix "fuzzy" match (`rn.startswith(qn) or
    qn.startswith(rn)`) as if it were exact, and fell back to the API's
    first result of any kind when nothing matched at all. Both were silent
    footguns: a sequel's suggestion ("Maa Inti Bangaaram 2") starts with the
    original's normalized title, so the original film's lookup returned the
    SEQUEL's tconst; and the blind first-result fallback attached whatever
    IMDb ranked highest for a garbled query, unrelated title or not. Wrong
    is worse than missing here — a wrong id silently corrupts rating,
    votes, cast and poster for that title — so this only returns something
    it can actually stand behind.

    When more than one title matches exactly (a generic word or short show
    name reused across unrelated movies/shows/decades — "Lioness" alone
    matches a well-known 2023 Paramount+ series AND an obscure unrelated
    2021 one, both tvSeries), narrow first by media type (movie vs series,
    via the API's own `qid`), discard any year that's flatly implausible
    (more than a year off for a premiere; later than the item's own air
    date for a returning season, which can't have debuted in the future),
    then let IMDb's own `rank` field settle it — its internal popularity/
    prominence ranking is a far more reliable tiebreaker than year ever is:
    an actually-airing returning show is essentially always dramatically
    more prominent than a same-titled one-off it happens to share a year
    with.
    """
    def to_hit(d):
        return {"id": d["id"], "title": d.get("l"), "year": d.get("y"),
                "qid": d.get("qid"), "rank": d.get("rank"),
                "img": ((d.get("i") or {}).get("imageUrl"))}

    def pick_best(hits):
        if len(hits) == 1:
            return hits[0]

        def plausible(h):
            y = h.get("year")
            if not year_hint or y is None:
                return True
            return y <= year_hint + 1 if allow_older else abs(y - year_hint) <= 1

        candidates = [h for h in hits if plausible(h)] or hits
        ranked = [h for h in candidates if h.get("rank") is not None]
        if ranked:
            return min(ranked, key=lambda h: h["rank"])
        return candidates[0]

    q = re.sub(r"\(.*?\)", "", title)
    q = re.sub(r"season\s*\d+", "", q, flags=re.I).strip()
    if not q:
        return None
    qn = norm_title(q)
    candidates = _imdb_suggest(q)
    # A "Creator's Show Name" title (common in TV branding, e.g. "Tyler
    # Perry's Ruthless") often has no IMDb entry under that exact string --
    # the platform's own page is titled just "Ruthless". Retry once with
    # the possessive prefix stripped before giving up.
    stripped = POSSESSIVE_PREFIX.sub("", q).strip()
    stripped_qn = norm_title(stripped) if stripped != q else None
    if stripped_qn:
        candidates = candidates + _imdb_suggest(stripped)
    seen_ids, uniq = set(), []
    for d in candidates:
        tid = d.get("id", "")
        if not tid.startswith("tt") or tid in seen_ids:
            continue
        seen_ids.add(tid)
        uniq.append(d)

    wanted_qids = QID_FOR_TYPE.get(item_type)
    exact = [to_hit(d) for d in uniq
             if norm_title(d.get("l", "")) in (qn, stripped_qn)]
    if wanted_qids:
        typed_exact = [h for h in exact if h.get("qid") in wanted_qids]
        if typed_exact:
            return pick_best(typed_exact)
        # The exact title matches that exist are the wrong media type (or
        # there were none) -- last resort: the source's title may simply be
        # a different language/region variant of IMDb's own primary title
        # (e.g. TVMaze's "No tengo miedo" vs IMDb's "I'm Not Afraid"). If
        # media type + year together narrow ALL suggestions (not just
        # title-exact ones) down to exactly one candidate, two independent
        # strong signals agreeing is safer than trusting a title string
        # that may not even be in the same language.
        if year_hint:
            typed_only = [to_hit(d) for d in uniq
                          if d.get("qid") in wanted_qids and d.get("y") == year_hint]
            if len(typed_only) == 1:
                return typed_only[0]
        if exact:
            return pick_best(exact)
        return None
    if not exact:
        return None
    return pick_best(exact)


def imdb_poster(img_url, width=500):
    """Amazon media CDN URL resized via the _V1_UX transform."""
    if not img_url or "._V1_" not in img_url:
        return img_url
    base = img_url.split("._V1_")[0]
    return f"{base}._V1_UX{width}_.jpg"


def wikidata_facts(tconsts):
    """Runtime (P2047) and poster image (P18) for IMDb ids via Wikidata.
    Returns {tconst: {"runtime": int|None, "image": commons_url|None}}."""
    out = {}
    ids = sorted(t for t in tconsts if t)
    if not ids:
        return out
    values = " ".join(f'"{t}"' for t in ids)
    q = ("SELECT ?tt ?dur ?img WHERE { VALUES ?tt { " + values + " } "
         "?item wdt:P345 ?tt . "
         "OPTIONAL { ?item wdt:P2047 ?dur . } "
         "OPTIONAL { ?item wdt:P18 ?img . } }")
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "decidemyshow/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        for b in data["results"]["bindings"]:
            tt = b["tt"]["value"]
            rec = out.setdefault(tt, {"runtime": None, "image": None})
            if rec["runtime"] is None and b.get("dur"):
                rec["runtime"] = round(float(b["dur"]["value"]))
            if rec["image"] is None and b.get("img"):
                # Commons Special:FilePath URL — request a sized thumbnail so
                # it lands in the same ~500px format as other posters.
                rec["image"] = b["img"]["value"] + "?width=500"
    except Exception as e:
        print(f"  ! wikidata facts failed: {e}", file=sys.stderr)
    return out


# A bare title (no Wikipedia disambiguator) very often belongs to something
# else entirely -- a city, a person, a book -- since Wikipedia gives the
# unqualified name to whichever topic is "primary". Matching by title alone
# is not enough; the returned page's own description/extract must actually
# sound like the kind of media we're looking for, or it gets rejected wholesale
# rather than risk showing an unrelated photo as if it were a poster.
FILM_DESC = re.compile(r"\b(film|movie)\b", re.I)
SERIES_DESC = re.compile(r"\b(television series|tv series|web series|anime series|"
                          r"drama series|limited series|documentary series|"
                          r"talk show|reality series|animated series)\b", re.I)
NOT_MEDIA_DESC = re.compile(
    r"\b(city|town|village|district|river|mountain|state|country|county|"
    r"municipality|metropolitan|politician|footballer|cricketer|singer|"
    r"company|university|school|college|airport|railway station|lake|"
    r"island|language|ethnic group|tribe|neighbourhood|neighborhood)\b", re.I)


def _wiki_summary_is_valid(data, expect_type):
    if data.get("type") == "disambiguation":
        return False
    desc = f"{data.get('description', '')} {(data.get('extract') or '')[:220]}"
    if expect_type == "movie":
        wanted, unwanted = FILM_DESC, SERIES_DESC
    else:
        wanted, unwanted = SERIES_DESC, FILM_DESC
    if unwanted.search(desc) and not wanted.search(desc):
        return False          # right franchise, wrong media type (film vs series)
    if NOT_MEDIA_DESC.search(desc) and not wanted.search(desc):
        return False          # bare title collided with an unrelated real-world topic
    return True


def wikipedia_poster(title, item_type):
    """Last-resort poster: the infobox image from the English Wikipedia REST
    summary. Catches brand-new titles with no IMDb/Wikidata entry yet. Every
    candidate is validated against the title's own description before its
    image is trusted -- see _wiki_summary_is_valid."""
    base = re.sub(r"\s*\(.*?\)\s*", "", title).strip()
    # strip common theatrical-spinoff subtitles ("X: The Movie") so a
    # franchise title still resolves to its Wikipedia disambiguator, e.g.
    # "Mirzapur: The Movie" -> "Mirzapur" -> "Mirzapur (film)"
    base = re.sub(r":?\s*[-–]?\s*the\s+(movie|film)\s*$", "", base, flags=re.I).strip()
    if item_type == "movie":
        candidates = [f"{base} (film)", f"{base} (2026 film)", f"{base} (2025 film)", base]
    else:
        candidates = [f"{base} (TV series)", f"{base} (web series)",
                      f"{base} (2026 TV series)", base]
    for cand in candidates:
        slug = urllib.parse.quote(cand.replace(" ", "_"))
        try:
            data = http_json(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}", timeout=15)
        except Exception:
            continue
        if not _wiki_summary_is_valid(data, item_type):
            continue
        src = ((data.get("originalimage") or {}).get("source")
               or (data.get("thumbnail") or {}).get("source"))
        if src:
            # normalize a Wikipedia thumb width up to ~500px for crispness
            return re.sub(r"/(\d+)px-", "/500px-", src)
    return None


def _cache_hit_is_stale(hit, our_year, allow_older):
    """A cached lookup worth retrying under the current (stricter) matcher:
    either its year now fails the sanity check, or it never had a year to
    check in the first place. A year-less hit is exactly what the old loose
    prefix-matching logic produced for a wrong same-prefix sequel match
    (e.g. "Maa Inti Bangaaram 2" for a search on the original) — IMDb's
    suggestion API often omits `y` for exactly that kind of entry, so it's
    unverifiable and cheap to just look up again."""
    if not our_year:
        return False
    year = hit.get("year")
    if not year:
        return True
    if allow_older:
        return year > our_year + 1
    return abs(our_year - year) > 1


def enrich_imdb(items):
    """Attach imdbId/imdbUrl and live rating+votes from IMDb's daily dataset."""
    try:
        with open(IMDB_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}

    looked = 0
    for it in items:
        if it.get("type") == "sport":
            continue
        # A curated entry may hardcode its own imdbId (and often poster) for
        # titles too ambiguous for the suggestion API to resolve — e.g. "DC",
        # which the index ranks below DC Comics. Trust it and skip the lookup,
        # so a wrong same-named match can never overwrite a verified id.
        if it.get("imdbId"):
            if not it.get("imdbUrl"):
                it["imdbUrl"] = f"https://www.imdb.com/title/{it['imdbId']}/"
            continue
        key = norm_title(it["title"])
        our_year = int(it["date"][:4]) if it.get("date") else None
        is_returning_season = it.get("type") == "series" and (it.get("season") or 1) > 1
        # re-lookup entries cached before poster support was added, or ones a
        # past bad match permanently blanked out (never worth retrying the
        # exact same wrong id forever)
        stale = key in cache and cache[key] is not None and _cache_hit_is_stale(
            cache[key], our_year, is_returning_season)
        if key not in cache or stale or (cache[key] is not None and "img" not in cache[key]):
            cache[key] = imdb_lookup_id(it["title"], item_type=it.get("type"),
                                        year_hint=our_year, allow_older=is_returning_season)
            looked += 1
        hit = cache.get(key)
        if hit:
            # A same-titled but unrelated production (an older reused title,
            # a same-named sequel/franchise entry) must still be rejected
            # even after an exact string match — the year is the only
            # remaining signal. A returning season's own debut may predate
            # this episode by any margin, but can't be LATER than it.
            year = hit.get("year")
            if year and our_year:
                if is_returning_season:
                    if year > our_year + 1:
                        continue
                elif abs(our_year - year) > 1:
                    continue
            it["imdbId"] = hit["id"]
            it["imdbUrl"] = f"https://www.imdb.com/title/{hit['id']}/"
            if not it.get("poster") and hit.get("img"):
                it["poster"] = imdb_poster(hit["img"])
    with open(IMDB_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)

    # Ratings from the previous run — the daily dump sometimes lags brand-new
    # titles, so a previously seen rating beats a temporary hole in the data.
    prev = {}
    try:
        with open(OUT_JSON, encoding="utf-8") as f:
            for old in json.load(f).get("items", []):
                if old.get("imdbId") and old.get("imdbRating"):
                    prev[old["imdbId"]] = (old["imdbRating"], old.get("imdbVotes"))
    except Exception:
        pass

    wanted = {it.get("imdbId") for it in items if it.get("imdbId")}
    rated = 0
    if wanted:
        try:
            req = urllib.request.Request("https://datasets.imdbws.com/title.ratings.tsv.gz",
                                         headers={"User-Agent": "decidemyshow/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                blob = r.read()
            ratings = {}
            with gzip.open(io.BytesIO(blob), "rt", encoding="utf-8") as f:
                next(f)
                for line in f:
                    tconst, avg, votes = line.rstrip("\n").split("\t")
                    if tconst in wanted:
                        ratings[tconst] = (avg, int(votes))
            for it in items:
                tid = it.get("imdbId")
                if tid and tid in ratings:
                    it["imdbRating"], it["imdbVotes"] = ratings[tid]
                    rated += 1
                elif tid and tid in prev:
                    it["imdbRating"], it["imdbVotes"] = prev[tid]
                    rated += 1
        except Exception as e:
            print(f"  ! imdb ratings dataset failed: {e}", file=sys.stderr)

    # Wikidata pass: fetch for any item that still needs a runtime (movies)
    # OR still needs a poster (movies + series) and has an IMDb id to join on.
    need_wd = {it["imdbId"] for it in items if it.get("imdbId")
               and ((it.get("type") == "movie" and not it.get("runtime"))
                    or not it.get("poster"))}
    facts = wikidata_facts(need_wd)
    timed = wd_posters = 0
    for it in items:
        f = facts.get(it.get("imdbId"))
        if not f:
            continue
        if it.get("type") == "movie" and not it.get("runtime") and f["runtime"]:
            it["runtime"] = f["runtime"]
            timed += 1
        if not it.get("poster") and f["image"]:
            it["poster"] = f["image"]
            wd_posters += 1

    # Final fallback: English Wikipedia infobox image for anything STILL
    # missing a poster (typically brand-new titles with no IMDb page).
    wiki_posters = 0
    for it in items:
        if it.get("poster") or it.get("type") == "sport":
            continue
        src = wikipedia_poster(it["title"], it.get("type"))
        if src:
            it["poster"] = src
            wiki_posters += 1

    still = sum(1 for it in items if not it.get("poster") and it.get("type") != "sport")
    print(f"  imdb: {looked} new lookups, {len(wanted)} ids, {rated} live ratings, "
          f"{timed} runtimes | posters via wikidata:{wd_posters} wikipedia:{wiki_posters}, "
          f"{still} still without art")


# ----------------------------------------------------------------- merge ----

def main():
    print("Release Radar — fetching…")
    with open(CURATED, encoding="utf-8") as f:
        curated = json.load(f)
    curated_items = curated.get("items", [])
    for it in curated_items:
        it.setdefault("source", "curated")
    print(f"  curated: {len(curated_items)} items")

    seen = {norm_title(it["title"]) for it in curated_items}
    merged = list(curated_items)
    for it in fetch_tvmaze() + fetch_tmdb():
        key = norm_title(it["title"])
        if key in seen or not it.get("date"):
            continue
        seen.add(key)
        merged.append(it)

    def sort_key(it):
        return (not it.get("featured", False), it.get("date") or "2099-12-31")
    merged.sort(key=sort_key)

    enrich_imdb(merged)

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "window": {
            "from": (date.today() - timedelta(days=DAYS_BACK)).isoformat(),
            "to": (date.today() + timedelta(days=DAYS_AHEAD)).isoformat(),
        },
        "counts": {"total": len(merged)},
        "items": merged,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    os.makedirs(os.path.dirname(OUT_JS), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("window.RELEASES = ")
        json.dump(out, f, ensure_ascii=False)
        f.write(";\n")
    print(f"  wrote {OUT_JSON} and {OUT_JS} — {len(merged)} total items")


if __name__ == "__main__":
    main()

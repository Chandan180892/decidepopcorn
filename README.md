# 🍿 DecidePopcorn — weekly movies & OTT board (India + Worldwide)

A single board for everything releasing this week and next: theatrical movies,
OTT premieres and live sports — with separate **🇮🇳 India** and **🌍 Worldwide**
views, filters by platform/language/genre/type, cast, episode counts, posters
and trailer links.

**Open the board:** `docs/index.html` (works offline — data is embedded).
Enable GitHub Pages (Settings → Pages → deploy from branch, `/docs` folder) to
get a hosted URL.

### How it stays fresh (auto-refresh every 2 days)

| Piece | What it does |
|---|---|
| `data/curated.json` | Hand-researched titles maintained by the **media-scout** agent. Highest priority. |
| `scripts/fetch_releases.py` | Merges curated data + TVMaze API (keyless) + TMDB API (optional key) into `data/releases.json` and `docs/data.js`. |
| `.github/workflows/refresh-media.yml` | Cron every 2 days at 08:00 IST — re-runs the fetcher and commits refreshed data. Change the cron to `30 2 * * *` for daily. Also runs on manual dispatch and whenever curated data changes. |
| `.claude/agents/media-scout.md` | Claude Code agent that web-researches the week's releases like a media expert and updates `data/curated.json`. |

### Refresh flows

- **Automatic (every 2 days):** GitHub Actions re-pulls TVMaze/TMDB and
  refreshes dates, posters and new premieres.
- **Deep research refresh:** in a Claude Code session on this repo, say
  *"use the media-scout agent to refresh the release board"* — it researches
  the current week across Indian + worldwide sources and rewrites the curated
  list.
- **Manual:** edit `data/curated.json`, then `python3 scripts/fetch_releases.py`.

### Optional: richer data with TMDB

Add a free [TMDB API key](https://www.themoviedb.org/settings/api) as a
repository secret named `TMDB_API_KEY`. The fetcher then adds upcoming movies
for India + US with real posters, verified trailer YouTube links, cast and
ratings. Without it the board still works on curated + TVMaze data.

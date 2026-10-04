# Foothold

> Repository name: `job-application-pipeline`. The product is called **Foothold** in the app; change the name with `APP_NAME` in `.env`.

Finds jobs that fit **your resume**, ranks them, and shows them in a clean local dashboard with an
**Apply** link for each one. It never applies for you — you click through and apply yourself.

```
resume PDF ─► profile (skills, level, roles) ─┐
                                               ├─► score each job 0-100 ─► SQLite ─► dashboard
Greenhouse · Lever · Ashby · Himalayas ·      ─┘        ▲
Remotive · Wellfound + YC (via TinyFish) ·          daily cron
(Adzuna, Arbeitnow optional)
```

## Features

- **Resume-aware ranking** — skills, seniority (intern / entry / mid / senior), title fit and location, scored 0–100. Every job's
  ring says how well it fits you, and opening a job shows exactly how that number was made. Built for freshers, editable for any level.
- **Many sources, one list** — ~110 company boards (Greenhouse, Lever, Ashby), Himalayas, Remotive, and Wellfound + YC
  "Work at a Startup" via TinyFish (search **and** Wellfound listing pages). Duplicates across sources are merged.
- **Salary in LPA** — shown on each card (foreign currencies converted and marked `≈`), with a **multi-select** salary filter
  (tick several ranges at once).
- **Colour-coded at a glance** — match ring, level, status, remote, salary and source each have a fixed colour, all checked for
  readable contrast. See [Design and colour coding](#design-and-colour-coding).
- **Filters you can add and remove** — search, score, level, India/Remote, source, posted-within, company, salary ranges, sort.
  Every active filter is a removable chip and lives in the URL.
- **Track your applications** — Save, Mark applied, Dismiss. *Active* shows what you still have to act on (new + saved); applied and
  dismissed jobs move to their own tabs (un-marking brings them back). Status survives re-crawls.
- **Cold email to YC founders** — a second page in the same app (navbar → *Cold email*): pick a YC batch, see founders and
  likely email addresses, and write personalised drafts from two plain boxes. Nothing is ever sent automatically.
- **Daily refresh, with or without your laptop** — a cron job that catches up after the machine was off, or (optional) a GitHub
  Action that crawls on GitHub's servers and your laptop imports the result. Plus a *Refresh jobs* button.
- **Local and private** — everything lives in a SQLite file on your machine; the dashboard has no login and only listens on `127.0.0.1`.

---

## Run it locally

### Prerequisites

| Need | Notes |
|---|---|
| Linux or macOS with `bash` | the helper scripts are bash (on Windows use WSL) |
| Python 3.10+ with `venv` | developed and tested on 3.12. On Debian/Ubuntu: `sudo apt install python3-venv` |
| Git | to clone |
| A resume as **PDF** (or TXT/MD) | text-based PDF, not a scan |
| *(optional)* a free TinyFish API key | only for Wellfound + YC — get one at <https://agent.tinyfish.ai> |

Node.js is **not** needed. Playwright's Chromium (downloaded by the setup script) is only used by the tests.

### 1. Clone

```bash
git clone git@github.com:Hanish2022/job-application-pipeline.git
cd job-application-pipeline
```

(or `https://github.com/Hanish2022/job-application-pipeline.git` if you don't use SSH keys)

### 2. Install

```bash
./scripts/setup.sh
```

Creates `.venv/`, installs the pinned dependencies from `requirements.txt`, and downloads Chromium for the tests
(~150 MB; skip it with `.venv/bin/pip install -r requirements.txt` if you don't plan to run the browser tests).

### 3. Configure (optional)

```bash
cp .env.example .env     # .env is git-ignored
```

| Variable | Needed for |
|---|---|
| `TINYFISH_API_KEY` | Wellfound + YC discovery. Leave empty to skip that source; everything else works. |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | optional extra India listings (free keys: <https://developer.adzuna.com>) |
| `FEED_REPO_URL` | private repo that receives the daily GitHub-Actions feed (see [GitHub Actions](#run-the-crawl-on-github-actions-optional-works-while-your-laptop-is-off)) |
| `APP_NAME` | the product name in the navbar and page titles (default `Foothold`) |
| `JOBS_RESUME` | path to your resume, if it isn't `~/Downloads/new_resume.pdf` |

### 4. Give it your resume

Any **one** of these:

1. **In the dashboard** (easiest): start the app (next step), open **Profile → Upload new resume**.
2. Put your PDF at `~/Downloads/new_resume.pdf`.
3. Set `JOBS_RESUME=/path/to/resume.pdf` in `.env`.
4. CLI: `.venv/bin/python -m app.cli profile --resume /path/to/resume.pdf`

Only the extracted profile (skills, level, roles, your name) is stored, in `data/jobs.db`. Phone/email lines are not kept.

### 5. Start it

```bash
./scripts/run.sh
```

Open **<http://127.0.0.1:8000>**.

- If a resume was found, the first run crawls all sources (about 1–2 minutes) before the server starts.
- If not, the server starts straight away: upload your resume in **Profile**, then click **Refresh jobs**.
- Change the port with `PORT=9000 ./scripts/run.sh`.

### 6. Refresh daily with cron (optional)

```bash
./scripts/install_cron.sh --print                 # show the lines, change nothing (default)
./scripts/install_cron.sh --install --time 08:30  # add them to your crontab (idempotent: re-running replaces them)
./scripts/install_cron.sh --status                # what's installed + last log lines
./scripts/install_cron.sh --remove                # take them out again
```

`--install` adds three crontab lines that all call `scripts/cron_crawl.sh`: at your chosen time every day, **every hour**, and
**at boot**. The script only crawls when a run is *due* (no successful run since today's slot, like anacron), so you get exactly
one crawl per day — and if your laptop was off or asleep at 08:30, it catches up at the next hourly tick or after a reboot.
Overlapping runs are skipped with `flock`. Output goes to `data/logs/crawl.log` (quiet hours leave no log lines).

Check by hand whether a run is due: `.venv/bin/python -m app.cli due --at 08:30` (exit 0 = due, 1 = not due).
Cron needs the machine to be on and the cron service running (`systemctl is-active cron`); it can't run while the computer is off.
The TinyFish key is read from `.env`, so cron needs no extra setup. Pages that were already rejected (stale, US-only…) are
remembered for 14 days so the daily run doesn't re-fetch them.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `python3 -m venv` fails | `sudo apt install python3-venv` (Debian/Ubuntu) |
| Cron didn't run | `systemctl is-active cron`; `./scripts/install_cron.sh --status`; read `data/logs/crawl.log` |
| Dashboard says "No jobs yet" and *Refresh* says to upload a resume | do step 4 |
| "resume has no extractable text" | your PDF is a scan/image — export a text PDF or use a `.txt` |
| Wellfound/YC jobs missing; run log says `TINYFISH_API_KEY not set` | add the key to `.env` |
| TinyFish `401` / "rejected the API key" | key is wrong or expired — create a new one |
| Few jobs with a salary | most postings don't publish pay; the sidebar shows how many do |
| Port already in use | `PORT=9000 ./scripts/run.sh` |
| Reset everything | stop the server, delete `data/`, run again |

---

## Run the crawl on GitHub Actions (optional, works while your laptop is off)

Cron needs your computer to be on. To have the crawl run on GitHub's servers instead, use the included workflow
(`.github/workflows/daily-job-feed.yml`). It crawls every day at 07:00 IST and publishes the result to a **private** repo; your
laptop then imports it and scores it with *your* resume.

```
GitHub Actions (daily)                     private data repo                    your laptop
crawl all sources, no resume   ──push──►   branch `feed` = one feed.db   ──►   sync-feed: filter + score with your
public postings only                       (overwritten, history never grows)   profile; your Save/Applied marks are kept
```

**Privacy by design:** the Action never sees your resume or profile, and the feed contains only public job postings. It is
still published to a *private* repo because it includes scraped listing text that shouldn't be republished publicly (and this
code repo may be public). The workflow refuses to run without a deploy key, so it can't fall back to publishing anywhere else.

### One-time setup (about 5 minutes, all in the GitHub web UI)

1. **Create a private, empty repo** for the data, e.g. `job-application-pipeline-data` (no README, no .gitignore).
2. **Create a deploy key** on your computer (no passphrase, used only for that repo):
   ```bash
   ssh-keygen -t ed25519 -N "" -C "jobs-feed-deploy" -f ~/.ssh/jobs_feed_deploy
   cat ~/.ssh/jobs_feed_deploy.pub      # public half
   ```
3. In the **data repo** → *Settings → Deploy keys → Add deploy key*: paste the **public** key and tick **Allow write access**.
4. In **this (code) repo** → *Settings → Secrets and variables → Actions → New repository secret*, add:
   - `FEED_DEPLOY_KEY` — the whole contents of `~/.ssh/jobs_feed_deploy` (the **private** key, including the `BEGIN`/`END` lines)
   - `TINYFISH_API_KEY` — your TinyFish key (without it Wellfound/YC are skipped; everything else still runs)
   - *(only if your data repo has a different name)* a repository **variable** `FEED_REPO` = `your-user/your-data-repo`
5. *Actions* tab → **daily-job-feed** → **Run workflow**. The first run creates the `feed` branch (about 1–2 minutes).
6. On your computer, add the data repo to `.env` (your normal SSH key must be able to read it) and sync:
   ```bash
   echo 'FEED_REPO_URL=git@github.com:<your-user>/<your-data-repo>.git' >> .env
   .venv/bin/python -m app.cli sync-feed
   ```

From then on **Refresh jobs** in the dashboard and the local cron job (`app.cli refresh`) pull the GitHub feed instead of
crawling locally. If the feed can't be fetched (offline, not published yet), they fall back to a local crawl and say so.
Without `FEED_REPO_URL` nothing changes: everything crawls locally as before.

Good to know:
- The schedule is in **UTC** (`30 1 * * *`), may start a few minutes late, and GitHub **pauses scheduled workflows in a
  repo with no activity for 60 days** — if the feed goes stale the dashboard run log says so; re-enable it in the Actions tab.
- Free for public repos; a private repo gets 2,000 free minutes/month (this uses about 2–3 minutes a day).
- The workflow only triggers on `schedule` and manual runs, never on pull requests, so forks can't reach your secrets.
- Manual commands: `app.cli crawl --generic` (the Action's mode), `app.cli sync-feed`, `app.cli import-feed path/to/feed.db`.

---

## Cold email to YC founders (navbar → *Cold email*)

A second page in the same app, at `/outreach`, for reaching out directly to founders of Y Combinator companies.
It is the open-source project [adityajha2005/yc-outreach](https://github.com/adityajha2005/yc-outreach) (MIT) included **unmodified** in
`vendor/yc-outreach/`, wearing this app's navbar and theme. Credit and licence: see `NOTICE`.

1. **Your name and links** (all optional): your name, and links to a portfolio, GitHub and resume. They only become a short signature at
   the bottom of the email; leave any blank and its line disappears. (Links only: nothing is uploaded.) Your name is pre-filled from
   your Jobs-page profile.
2. **Your message**: just two boxes. *A little about you* (one or two sentences in your own words) and *What are you looking for?*
   (an internship, a full-time job, or a short chat). The page writes the rest for every company: the greeting with the founder's first name,
   which company it is and what it does, your sentences, the ask, and your signature. A live preview shows the result. Nobody has to deal
   with templates or `{placeholders}`; the full text is available under *Advanced* if you want to edit it by hand (changing the two boxes
   afterwards rebuilds it).
3. **Pick a batch** and *Load companies*: founders load 20 at a time (about 5–10 s each time).
4. **Open a company**: it shows who the email goes to and how reliable the address is (**found on website** = the company publishes it;
   **guessed** = built from the founder's name, right about 80% of the time, so it may bounce; **verified** needs the optional
   Apify step, tucked under "Advanced"). Then *Copy message* / *Copy address* or *Open in email app*, and tick *Mark as sent*.

Everything you type (details, template, "sent" ticks, loaded batches) stays in your browser's `localStorage`; the server stores
nothing. **It never sends email**: you send each message yourself.

Please use it the way the original author asks: *write to people one at a time, keep it short, and take "no" for an answer.*
Personalise every message, don't send in bulk, and don't contact someone twice after they decline. Mass unsolicited email can breach
anti-spam law (for example CAN-SPAM, GDPR/PECR) and gets mail accounts blocked. The founder data is YC's public directory plus
addresses companies publish on their own sites.

**How it is integrated** (`app/outreach.py`): the vendored Python module is loaded as-is and served at `/api/yc`; its page is served
at `/outreach` with our navbar and `app/static/outreach.css` added around it. Two protections are added without touching its code:

- **SSRF guard.** The upstream server fetches each company's website, and that address comes from YC's data, which companies
  control. On a public host that is harmless; on your laptop beside a no-login dashboard, a hostile value such as
  `http://127.0.0.1:…` or a redirect to it could make your machine call its own local services. Every request and redirect hop must
  resolve to public addresses only (loopback, private, link-local, cloud-metadata, IPv4-in-IPv6 and odd encodings like `2130706433` are
  refused), the connection is pinned to the validated address, and only `http(s)` is allowed. The tests include real sockets attacking it.
- **Plain-language layer** (`app/static/outreach.js` + `outreach.css`): rewrites labels, adds the explanations and live preview, and reuses the
  page's own functions instead of copying them. It is defensive (if upstream changes shape it quietly does nothing) and a test lists every
  hook it depends on, so `update_outreach.sh --apply` followed by the tests tells you immediately if upstream renamed something.
- **Content-Security-Policy** on `/outreach`: the page may talk to itself and `api.apify.com` only, so a bug or hostile data cannot
  send your drafts elsewhere; it also cannot be framed.

**Updating the vendored project** (never edit `vendor/` by hand; a test checks the file hashes):

```bash
./scripts/update_outreach.sh            # shows what upstream changed; writes nothing
./scripts/update_outreach.sh --apply    # copies it in and refreshes vendor/yc-outreach/UPSTREAM.json
./scripts/test.sh                       # then run the tests
```

Read the diff before applying: this code runs on your machine, in the same origin as your dashboard.

---

## Design and colour coding

The look is a dark, Linear-style UI (`DESIGN.md`) with a small set of colours that each mean one thing, borrowed from the tag
system in Raycast's design spec (a saturated accent plus its 15%-opacity tint). Colour is used for *meaning*, never decoration:

| Where | Colour → meaning |
|---|---|
| Match ring | green **80+** · blue **60–79** · yellow **40–59** · gray below |
| Level tag | violet **internship** · green **entry/fresher** · yellow **mid** · red **senior** · gray not specified |
| Status | blue **saved** · green **applied** · faded **dismissed** (the row also gets a coloured edge) |
| Tags | blue **Remote** · yellow dashed **Preview** (found via search, details not confirmed) |
| Salary | green when listed, gray "not listed" |
| Email confidence (Cold email) | blue **found on website** · yellow **guessed/unverified** · green **verified** · gray none |
| Source dot | teal Greenhouse · blue Lever · violet Ashby · yellow Wellfound · orange YC · pink Himalayas (identity only) |
| Freshness dot (navbar) | green updated <26 h · yellow older/failed · pulsing blue crawling · gray never |

Navigation is one shared component (`app/shell.py`) and always a single sticky row: brand and page links grouped on the left, the
page's actions on the right (freshness pill, profile avatar and *Refresh* on Jobs; a "nothing is sent automatically" note on Cold
email). As the window narrows, text collapses to icons instead of wrapping or jumping elsewhere, down to 320 px wide; a test checks 17 window
widths on both pages for overlaps, wrapping and sideways scrolling. All colours are checked for WCAG contrast by
`tests/test_design_tokens.py`, which reads the actual CSS (tag text is ≥ 4.5:1 on its own tint over every surface; the brand violet is never
used for small text because it fails that test, so links and violet tags use a lighter violet).

**If the UI ever looks out of date:** asset URLs carry a content hash (`shell.css?v=…`) and the server tells browsers to revalidate on every
load, so a stale stylesheet can't be paired with new markup. The logo's tooltip shows the UI build id (e.g. `UI build 3fa9c1d2`), and the
`<header>` has a matching `data-ui-build` attribute, so you can tell which version is loaded. A hard refresh is `Ctrl+Shift+R`.

Component patterns (floating pill nav with an active marker, sliding tab indicator, soft-tinted tags, KPI cards with icon tiles) are
common UI patterns implemented from scratch in plain CSS/JS. **No code was copied from 21st.dev**: its components are React + Tailwind
and belong to their individual authors, and this project has no build step.

## Using the dashboard

- **Tabs:** *Active* (new + saved), *Saved*, *Applied*, *Dismissed*; counts are shown on the tabs. Marking a job applied moves it out
  of Active immediately.
- **Each job:** a match ring (hover it, or see the legend above the list), colour-coded level / *Remote* / status tags, your matching
  skills, salary in LPA and the source. **Apply** always opens the original posting in a new tab.
- **Click a title** for the description, *why it matched* and **How the score is made** (four bars: skills, role fit, seniority, location;
  plus a note when a cap, such as "senior role", applied).
- **Filters** are in the sidebar; each active one is a removable chip and the whole view is in the URL, so it can be bookmarked. The
  salary filter is a set of checkboxes (see [Salary](#salary-lpa)); counts next to each range show how many active jobs fall in it.
- **Profile** lets you edit skills, locations, level and "hide titles containing…" keywords, or upload a new resume;
  all jobs are re-scored instantly.
- A dashed **Preview** badge means the job was found through search and its page couldn't be read yet — open it to confirm.
- The dot next to *Last refreshed* in the navbar shows freshness: green (under a day), yellow (older or failed), pulsing blue
  (crawling), gray (never).

Design follows the Linear-inspired `DESIGN.md` from getdesign.md (an independent analysis; not affiliated with Linear); the colour-tag
system follows Raycast's.

## How matching works (`app/matcher.py`)

`score = skills (0-50) + title fit (0-25) + seniority fit (0-20) + location (0-5)`

The number on each job's ring is this score: **how well the job fits your resume, out of 100** (green 80+, blue 60–79, yellow 40–59,
gray below). Open a job to see the four parts as bars ("How the score is made") and, if a cap applied, why. The skills part has two
halves so scores spread out instead of piling up at 100: *depth* (how many of your skills the job uses, with diminishing returns) and
*coverage* (what share of what the job asks for you actually have). A perfect 100 is effectively unreachable.

- Skills come from a vocabulary (`app/skills.py`) read from both resume and job text; skills your resume leans on
  weigh more, near-misses (e.g. TypeScript ↔ JavaScript) earn partial credit.
- Seniority is inferred from the title (`Intern`, `SDE I`, `Junior`, `Senior`, `(0-2 years)`…), source tags, and
  "N years of experience" in the description. Senior roles are capped at 25, mid-level at 55, non-engineering titles at 20.
- "Remote" only counts if you can take it from India (`Remote – US only` does not).
- Deterministic and explainable; no LLM calls.

## Salary (LPA)

Pay comes from source fields (Himalayas, Lever, Adzuna, Wellfound/YC pages) or is parsed from text like
`₹12–18 LPA`, `INR 1,800,000–2,200,000 annual`, `$150K–$200K`, `₹1.2L – ₹1.8L`. Monthly/hourly figures are annualised.
Foreign currencies use fixed approximate rates (`FX_TO_INR` in `app/salary.py`, edit to taste) and are shown with `≈`.
The **Salary filter is a multi-select**: tick as many ranges as you like (*Under ₹5*, *₹5–10*, *₹10–15*, *₹15–25*, *₹25–40*,
*₹40 and above*, *Not listed*) and a job shows if it matches **any** of them. A job matches a range when its advertised range
overlaps it (₹12–18 LPA appears under both ₹10–15 and ₹15–25; a range that ends exactly where another begins does not spill over).
Each range shows how many active jobs fall in it, *Any listed* ticks all the ranges, and jobs with no salary only appear if you tick
*Not listed* (or tick nothing). The API takes `?salary=5-10,10-15,none` (plus the older `min_lpa` / `has_salary`).

## Sources

| Source | Access | Notes |
|---|---|---|
| Greenhouse, Lever, Ashby | public job-board APIs | ~110 company boards in `companies.json` (verified). Add any company by its board token. |
| **Wellfound, YC Work at a Startup** | **TinyFish** search + fetch (needs `TINYFISH_API_KEY`) | see below |
| Himalayas | public API | entry-level + India searches; please keep attribution (source shown on each card) |
| Remotive | public API | remote software jobs |
| Adzuna | free API key | optional |
| Arbeitnow | public API | Europe-centric, disabled by default |

### Wellfound + YC via TinyFish

Neither site has a public jobs API and this project never logs in to them. Instead [TinyFish](https://docs.tinyfish.ai) does the work,
through two discovery channels that feed the same pipeline:

1. **Search** returns public posting URLs for queries such as `site:wellfound.com/jobs "junior" "full stack" remote`
   (20 queries in `companies.json` → `aggregators.tinyfish.queries`; edit freely). Deeper result pages are only requested while
   a page still brings jobs we haven't seen, so daily runs stay cheap.
2. **Listing pages** (`role_pages`, e.g. `wellfound.com/role/r/full-stack-developer`, `…/role/l/software-engineer/india`): one fetch returns up to
   ~50 current postings. (YC's listing pages ignore filters, so YC is covered through search only.)

Every new candidate is then **opened and judged on the real page**, not on its search-result label. This matters: Wellfound labels a
search result with the *company's* home city ("Junior Full-Stack Developer at Nexorlio • Las Vegas") even when the job itself is
"Remote only • Everywhere". Judging on the label used to throw such jobs away unseen, which is how a matching remote job was once missed.
From the page we read location, remote policy, pay, experience, posted date and description, so these jobs are scored, filtered and
LPA-tagged like any other.

Guards against stale/wrong results: closed-job text, postings older than `max_age_days`, US-work-authorisation-only roles and
non-engineering titles are dropped; a page that is permanently gone (`page_not_found`) is remembered and never fetched again; a
temporary failure (`bot_blocked`, timeout) is kept as a **Preview** and retried next run. The per-run fetch budget (`max_fetch`) is
shared fairly between Wellfound and YC (round-robin), and promising candidates (India/remote, junior titles) are read first.
Jobs that stop appearing in search for `close_after_days` are closed.

**See what a run did.** After `crawl`, the last run's log shows what the TinyFish source did (search calls, listing-page jobs,
pages opened, how many were kept, removed, stale or US-only):

```bash
.venv/bin/python -c "import json,sqlite3; c=sqlite3.connect('data/jobs.db'); print(json.loads(c.execute('select stats from runs order by id desc limit 1').fetchone()[0])['sources']['tinyfish'])"
```

To judge whether a *new* query is worth adding, run it as a search by hand and compare the results with what you already have:
queries that mostly return postings already in your list only cost calls.

Things to know: TinyFish sees your search queries and the URLs fetched (not your resume). Wellfound's and YC's own terms of
service still apply to how you use their listings; this tool only finds public postings and sends you to the original page to
apply. Search-based discovery can lag behind reality, which is why the dead/stale checks and the Preview badge exist.

**Not included on purpose:** Naukri, Indeed and LinkedIn — their terms forbid scraping, and the only ways in are logged-in
scraping (account-ban risk) or paid scraping services.

Add companies: edit `companies.json` (`greenhouse`, `lever`, `ashby` arrays). A wrong token just logs a warning; it never
stops the run.

## Commands

```bash
.venv/bin/python -m app.cli crawl                       # fetch + score locally now
.venv/bin/python -m app.cli crawl --generic             # feed mode (what the GitHub Action runs): no resume, no scoring
.venv/bin/python -m app.cli refresh                     # what cron and the Refresh button run: GitHub feed if FEED_REPO_URL is set, else crawl
.venv/bin/python -m app.cli sync-feed                   # fetch + import the GitHub feed
.venv/bin/python -m app.cli import-feed path/feed.db    # import a feed file you already have
.venv/bin/python -m app.cli profile --resume cv.pdf     # (re)build profile from a resume
.venv/bin/python -m app.cli due --at 08:30              # exit 0 if a scheduled crawl is due (used by cron)
.venv/bin/python -m app.cli stats                       # database summary
.venv/bin/python -m app.cli serve --port 8000           # dashboard only (no first-run crawl)
./scripts/test.sh                                       # all tests
```

## Tests

`./scripts/test.sh` runs ~590 tests (about 130 of them in a real browser) against throw-away databases; your real `data/jobs.db`
and API keys are never touched:

- **Unit:** text utils, store/dedupe/filters (including salary ranges and the Active/Applied split), each source parser, retry logic,
  salary parsing, resume parsing, the matcher (incl. score spread and breakdown), pipeline and TinyFish discovery with HTTP mocked
  (incl. a replay of the remote-job-labelled-with-a-city case), the GitHub feed, cron scheduling, cache-busting of assets.
- **Design and HTML:** colour contrast computed from the actual CSS tokens, colour-meaning consistency, well-formed markup, a
  check that the product name lives in one place.
- **Security:** the Cold-email SSRF guard is attacked with real sockets (loopback, redirects, IPv6, odd IP encodings, `file://`);
  the GitHub workflow is checked for safe triggers and secret handling; vendored code is hash-checked.
- **Playwright/Chromium end-to-end** (`tests/e2e`): filters and URL state, save/apply/dismiss, drawers and focus handling, profile edit
  and resume upload, the refresh flow, error states, XSS safety, accessibility contract, layout geometry, a navbar sweep over 17
  window widths on both pages, and the Cold-email page.

Skip the browser tests with `./scripts/test.sh --ignore=tests/e2e`.

## Layout

```
app/
  main.py          FastAPI app: API, pages, security headers
  cli.py           command line (crawl / refresh / sync-feed / due / serve …)
  pipeline.py      crawl orchestration + scoring run; feed.py = GitHub feed import; schedule.py = "is a crawl due?"
  matcher.py       0–100 job ↔ resume score (+ breakdown); skills.py, resume.py, salary.py, textutil.py
  store.py         SQLite: jobs, filters, salary ranges, stats, runs, rejected-URL memory
  shell.py         the shared navbar; assets.py = cache-busting of static files
  outreach.py      integration of the vendored Cold-email project (SSRF guard, themed page)
  config.py        settings (.env), APP_NAME
  sources/         one module per source (greenhouse, lever, ashby, himalayas, remotive, tinyfish, …)
  static/          index.html, app.js, styles.css, shell.css (tokens + navbar), outreach.css/js  (no build step)
vendor/            third-party code, unmodified (yc-outreach, MIT) — update with scripts/update_outreach.sh; see NOTICE
scripts/           setup.sh run.sh test.sh cron_crawl.sh install_cron.sh publish_feed.sh restore_feed.sh update_outreach.sh
.github/           workflows/daily-job-feed.yml (the scheduled crawl)
tests/             unit tests + tests/e2e (Playwright)
companies.json     which boards / queries / listing pages to crawl
DESIGN.md          the design reference the UI follows
data/              jobs.db, logs (git-ignored, created on first run)
```

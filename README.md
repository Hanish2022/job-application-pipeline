# job-application-pipeline

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

- **Resume-aware ranking** — skills, seniority (intern / entry / mid / senior), title fit and location, scored 0–100 with a
  plain-English "why it matches". Built for freshers, but editable for any level.
- **Many sources, one list** — ~110 company boards (Greenhouse, Lever, Ashby), Himalayas, Remotive, and Wellfound + YC
  "Work at a Startup" via TinyFish. Duplicates across sources are merged.
- **Salary in LPA** — shown on each card (foreign currencies converted and marked `≈`), with a minimum-LPA filter.
- **Filters you can add and remove** — search, score, level, India/Remote, source, posted-within, company, salary, sort.
- **Track your applications** — Save, Mark applied, Dismiss; status survives re-crawls.
- **Daily refresh** — one-command cron job, plus a *Refresh jobs* button.
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

## Using the dashboard

- Ranked job list with match score, matched skills, level, location, salary (LPA), source and age.
- Every filter shows as a removable chip, and the state lives in the URL, so views can be bookmarked.
- Click a title for the full description and *why it matched*; **Apply** always opens the original posting in a new tab.
- **Profile** lets you edit skills, locations, level and "hide titles containing…" keywords, or upload a new resume;
  all jobs are re-scored instantly.
- A dashed **Preview** badge means the job was found through search and its page couldn't be read yet — open it to confirm.

Design follows the Linear-inspired `DESIGN.md` from getdesign.md (an independent analysis; not affiliated with Linear).

## How matching works (`app/matcher.py`)

`score = skills (0-50) + title fit (0-25) + seniority fit (0-20) + location (0-5)`

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
The *minimum LPA* filter matches when the top of the advertised range reaches your number; jobs with no salary are hidden
when it is set.

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

Neither site has a public jobs API and this project never logs in to them. Instead [TinyFish](https://docs.tinyfish.ai) does the work:

1. **Search** returns public posting URLs for queries such as `site:wellfound.com/jobs "full stack" India intern`
   (queries live in `companies.json` → `aggregators.tinyfish.queries`; edit freely).
2. For results not seen before, **Fetch** renders the posting and we read location, remote policy, pay, experience,
   posted date and description, so these jobs are scored, filtered and LPA-tagged like any other.
3. Guards against stale/wrong results: closed-job text, postings older than `max_age_days`, US-work-authorisation-only
   roles and non-engineering titles are dropped. A result whose page couldn't be fetched (`bot_blocked`, timeout) is kept
   as a **Preview** and retried next run. Jobs that stop appearing in search for `close_after_days` are closed.
4. Already-understood jobs are never re-fetched, and fetches are capped per run (`max_fetch`) to respect the free tier.

Things to know: TinyFish sees your search queries and the URLs fetched (not your resume). Wellfound's and YC's own terms of
service still apply to how you use their listings; this tool only finds public postings and sends you to the original page to
apply. Search-based discovery can lag behind reality, which is why the dead/stale checks and the Preview badge exist.

**Not included on purpose:** Naukri, Indeed and LinkedIn — their terms forbid scraping, and the only ways in are logged-in
scraping (account-ban risk) or paid scraping services.

Add companies: edit `companies.json` (`greenhouse`, `lever`, `ashby` arrays). A wrong token just logs a warning; it never
stops the run.

## Commands

```bash
.venv/bin/python -m app.cli crawl                       # fetch + score now
.venv/bin/python -m app.cli profile --resume cv.pdf     # (re)build profile from a resume
.venv/bin/python -m app.cli stats                       # database summary
.venv/bin/python -m app.cli serve --port 8000           # dashboard only (no first-run crawl)
./scripts/test.sh                                       # all tests
```

## Tests

`./scripts/test.sh` runs ~300 tests against throw-away databases (your real `data/jobs.db` and API keys are never touched):

- unit: text utils, store/dedupe/filters, each source parser, retry logic, salary parsing, resume parsing, matcher,
  pipeline and TinyFish discovery (HTTP mocked), API, cron scripts (with a fake `crontab`)
- Playwright/Chromium end-to-end (`tests/e2e`): filters, URL state, save/apply/dismiss, drawers + focus handling,
  profile edit + resume upload, refresh flow, error state, XSS safety, accessibility contract, mobile layout, design tokens

Skip the browser tests with `./scripts/test.sh --ignore=tests/e2e`.

## Layout

```
app/            crawler, matcher, salary parser, store, API (main.py), CLI
app/sources/    one module per source (greenhouse, lever, ashby, himalayas, remotive, tinyfish, …)
app/static/     index.html, styles.css, app.js  (no build step)
scripts/        setup.sh run.sh test.sh cron_crawl.sh install_cron.sh publish_feed.sh restore_feed.sh
.github/        workflows/daily-job-feed.yml  (the scheduled crawl)
tests/          unit tests + tests/e2e (Playwright)
companies.json  which boards/queries to crawl
data/           jobs.db, logs (git-ignored, created on first run)
```

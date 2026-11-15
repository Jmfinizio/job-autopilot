# Job Autopilot

Finds jobs that fit you, tracks every application from your Gmail, and tells you what to do next.

- **Finds jobs:** checks company career boards (Greenhouse, Lever, Ashby) and, optionally, a broad job search (Adzuna) every 2 hours. It scores each posting 0–100 against your roles, locations, and keywords.
- **Tracks applications:** reads application, interview, assessment, rejection, offer, and recruiter emails and keeps a Google Sheet up to date. A status only ever moves forward, so a late "thanks for applying" email won't undo an interview.
- **Notifies you:**
  - A phone push alert right away for strong matches (score ≥ 75), interview invites, assessments, and offers
  - A morning email digest with new matches, application updates, and follow-ups that are due
- **Reminds you to follow up** when an application has been quiet for 10 days.

It runs on GitHub Actions in this **private** repo, so it works while your laptop is off and nobody can see you're looking. It's read-only on your inbox: it never marks emails read, moves them, or deletes them.

```
Company boards ─┐                                         ┌─► 📱 push alerts (ntfy)
Adzuna search ──┼─► score & filter ─┐                     │
                │                   ├─► Apps Script ─► Sheet ┼─► ✉️  morning digest
Gmail ──────────┴─► classify email ─┘   (in your Sheet)     │
                                                            └─► follow-up reminders
```

## Your Google Sheet

| Tab | What's in it |
|---|---|
| **Jobs** | Every match: score, company, title, location, link, and why it matched. **Type `Applied` in the Status column** and it moves to Applications on the next run. |
| **Applications** | One row per application: status, dates, follow-up date, last email. The **Notes** column is yours; the bot never edits it. |
| **Settings** | **Change your search here**, even from your phone: roles, locations, excluded titles, keywords, companies to watch, and alert threshold. Lists are comma-separated. |
| `_state` | Bookkeeping. You can hide this tab. |

## Setup (about 15 minutes, one time)

### 1. Google Sheet + its script (no Google Cloud needed)
1. Create a blank Google Sheet (name it e.g. "Job Tracker").
2. In the Sheet: **Extensions → Apps Script**. Delete the sample code and paste in everything from [`apps_script/Code.gs`](apps_script/Code.gs).
3. On the `SECRET` line, replace `PASTE-A-LONG-RANDOM-SECRET-HERE` with a long random password (mash the keyboard; 30+ characters). Save (⌘S).
4. **Deploy → New deployment →** gear icon **→ Web app**. Set **Execute as: Me** and **Who has access: Anyone**. Click **Deploy**.
5. Google asks you to authorize: pick your account → **Advanced → Go to (project) (unsafe) → Allow**. This is your own script, so the "unsafe" warning just means Google hasn't reviewed it.
6. Copy the **Web app URL** (ends in `/exec`).

> "Anyone" only means GitHub can reach the URL without a Google login. Every request must include your secret, or the script refuses it.
> If you change `Code.gs` later, use **Deploy → Manage deployments → ✏️ Edit → Version: New version → Deploy** so the URL stays the same.

### 2. Gmail
Nothing to set up. The Sheet script reads your application emails and sends your digest **as you**, so there are no passwords. The first deploy asks you to allow Gmail access; that permission only goes to your own script.

### 3. Phone notifications
1. Install **ntfy** ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. Tap **+** and subscribe to a topic with a long, random name, e.g. `jf-jobs-8c41e2b97d`. Anyone who knows the name can read it, so don't use something guessable.

### 4. (Optional) Broad job search
Get a free key at [developer.adzuna.com](https://developer.adzuna.com). Without it, only the company boards in Settings are searched.

### 5. Add the secrets to GitHub
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `SHEET_SCRIPT_URL` | the Web app URL from step 1.6 |
| `SHEET_SCRIPT_SECRET` | the same secret you typed into `Code.gs` |
| `NTFY_TOPIC` | your topic name |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | optional |

### 6. Test it
**Actions → Job Autopilot → Run workflow → command: `check`.** You should get a test push on your phone, and your sheet gets its tabs. Then run `all` once, and it's on autopilot from there.

## Choosing what to search for
Edit the **Settings** tab in the sheet (it wins over `config.yaml`):

- `roles`: `Data Engineer, Analytics Engineer, BI Engineer`
- `locations`: `Boston, New York, Remote` (Boston also catches Cambridge, Somerville, Waltham…)
- `exclude_titles`: `Principal, Director, Intern`
- `greenhouse_companies` / `lever_companies` / `ashby_companies`: the company's board name from its careers URL (`boards.greenhouse.io/`**`stripe`**, `jobs.lever.co/`**`palantir`**, `jobs.ashbyhq.com/`**`ramp`**). A wrong name shows up in the run log; it won't break anything.

## How scoring works
A posting only counts if its **title** matches a role, its **location** matches (or it's remote and you listed Remote), it isn't excluded, and it's newer than `max_age_days`. Then it starts at 50 and:

- **+10** for an exact role phrase in the title
- **+10** for an early-career title (Associate, Junior, I), or **−15** for a senior one (Senior, Lead, Staff)
- **+5 per keyword** you care about (Python, SQL, Azure, …), up to +25
- **+10** if it was posted in the last 2 days

Alerts go out at `alert_score` and above; everything at `min_score` and above lands in the sheet and the digest.

## Running it yourself
```bash
pip install -r requirements.txt
python run.py demo          # full run on sample data, no accounts needed → demo_digest.html
python -m pytest tests -q   # 23 tests
python run.py check         # with SHEET_SCRIPT_URL / SHEET_SCRIPT_SECRET / NTFY_TOPIC set
```

## Limits
- Email reading uses rules, not AI. It handles the common ATS formats (Greenhouse, Lever, Workday…), but an unusual email may get the wrong company or no role. Fix the row by hand; later emails still update it.
- LinkedIn and Indeed don't offer public job APIs, so they aren't searched directly. Adzuna covers many of the same postings.
- GitHub's schedule can run a few minutes late during busy times.
- A private repo gets 2,000 free Actions minutes a month. This uses about 400–600.

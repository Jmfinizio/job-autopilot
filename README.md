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
Company boards ─┐                                   ┌─► 📱 push alerts (ntfy)
Adzuna search ──┼─► score & filter ─┐               │
                │                   ├─► Google Sheet ┼─► ✉️  morning digest
Gmail (IMAP) ───┴─► classify email ─┘   (Jobs,       │
                                         Applications,└─► follow-up reminders
                                         Settings)
```

## Your Google Sheet

| Tab | What's in it |
|---|---|
| **Jobs** | Every match: score, company, title, location, link, and why it matched. **Type `Applied` in the Status column** and it moves to Applications on the next run. |
| **Applications** | One row per application: status, dates, follow-up date, last email. The **Notes** column is yours; the bot never edits it. |
| **Settings** | **Change your search here**, even from your phone: roles, locations, excluded titles, keywords, companies to watch, and alert threshold. Lists are comma-separated. |
| `_state` | Bookkeeping. You can hide this tab. |

## Setup (about 15 minutes, one time)

### 1. Google Sheet + service account (for writing the sheet)
1. Create a blank Google Sheet. Copy its ID from the URL: `docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.
2. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a project (any name) → **APIs & Services → Library** → enable **Google Sheets API**.
3. **APIs & Services → Credentials → Create credentials → Service account** (any name) → open it → **Keys → Add key → JSON**. A `.json` file downloads.
4. Open the JSON, copy the `client_email` value, and **share your Sheet with that email as Editor**.

### 2. Gmail app password (for reading application emails and sending the digest)
1. Make sure 2-Step Verification is on: [myaccount.google.com/security](https://myaccount.google.com/security).
2. Create an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (name it "Job Autopilot"). Copy the 16 characters.
3. Make sure IMAP is on: Gmail → Settings → See all settings → Forwarding and POP/IMAP → **Enable IMAP**.

### 3. Phone notifications
1. Install **ntfy** ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. Tap **+** and subscribe to a topic with a long, random name, e.g. `jf-jobs-8c41e2b97d`. Anyone who knows the name can read it, so don't use something guessable.

### 4. (Optional) Broad job search
Get a free key at [developer.adzuna.com](https://developer.adzuna.com). Without it, only the company boards in Settings are searched.

### 5. Add the secrets to GitHub
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `SHEET_ID` | the ID from step 1.1 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the **entire contents** of the JSON file from step 1.3 |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password |
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
python -m pytest tests -q   # 22 tests
python run.py check         # with secrets set as environment variables
```

## Limits
- Email reading uses rules, not AI. It handles the common ATS formats (Greenhouse, Lever, Workday…), but an unusual email may get the wrong company or no role. Fix the row by hand; later emails still update it.
- LinkedIn and Indeed don't offer public job APIs, so they aren't searched directly. Adzuna covers many of the same postings.
- GitHub's schedule can run a few minutes late during busy times.
- A private repo gets 2,000 free Actions minutes a month. This uses about 400–600.

# Grocery Society event collector

A small program that visits gallery websites once a day, reads their exhibitions and events, and saves them into one spreadsheet for you to check before anything goes on Grocery Society.

You don't need to know how to code to set it up. Follow the steps in order.

---

## What's in this folder

| File | What it is |
| --- | --- |
| `sources.csv` | The list of websites to visit. Open it in Google Sheets or Excel to add galleries. |
| `collect.py` | The program itself. You won't need to change it. |
| `output/events.csv` | The results: one row per event. This is the file you check. |
| `output/run_log.csv` | A short report: which sites worked, which were skipped and why. |
| `.github/workflows/daily.yml` | Tells GitHub to run the program every morning. |
| `tests/` | Practice pages and checks, so you can see it working without the internet. |

---

## Part 1: Get the two things it needs

**1. A Claude API key** (lets the program read pages that don't have tidy event data)

1. Go to the Claude Console at console.anthropic.com and create an account.
2. Add a small amount of credit. A few dollars lasts a long time: reading one page costs roughly a tenth of a US cent with Claude Haiku 5.5.
3. Create an API key and copy it somewhere safe. Never paste it into any file in this folder.

**2. A contact email** for galleries. The program tells every website who is visiting and how to reach you. Use the Grocery Society email.

---

## Part 2: Put it on GitHub (free, runs every day)

1. Create a free account at github.com.
2. Click **New repository**. Name it `gs-collector`. Choose **Private**. Click **Create**.
3. Click **uploading an existing file**, drag in everything from this folder (including the `.github` folder), and click **Commit changes**.
   - On a Mac, press Cmd + Shift + . in Finder to see the hidden `.github` folder.
4. Go to **Settings → Secrets and variables → Actions → New repository secret** and add two secrets:
   - Name `ANTHROPIC_API_KEY`, value: your API key.
   - Name `GS_CONTACT_EMAIL`, value: your contact email.
5. Go to the **Actions** tab. If asked, click to enable workflows.
6. Click **Collect events daily → Run workflow** to try it now.
7. When it finishes (a minute or two), open the `output` folder in your repository and click `events.csv`.

From now on it runs every morning by itself.

---

## Part 3: Check the results each day

Open `output/events.csv` in Google Sheets (File → Import → Upload). Each row is one event.

| Column | What it means |
| --- | --- |
| `status` | Worked out from the dates: **on now**, **closing soon** (ends within 7 days), **opening soon** (starts within 7 days), **upcoming**, or **check dates** |
| `needs_review` | **yes** = check it before publishing. Always yes when Claude read the page or a date or venue is missing |
| `found_by` | **structured data** (the site's own tidy details, most reliable) or **claude** (read from the page text) |
| `notes` | Anything uncertain, such as "missing: end date" or "also listed elsewhere" |
| `source_url` | The page it came from. Click it to check. |

Events that have already ended are left out automatically.

Then open `output/run_log.csv`:

| Result | What to do |
| --- | --- |
| ok | Nothing |
| needs AI | The API key secret is missing or wrong |
| skipped | The site asks bots not to visit. Email the gallery and ask for their calendar instead. |
| error | The page may have moved. Open the link in `sources.csv` and update it. |

---

## Part 4: Add more galleries

1. Open `sources.csv` on GitHub and click the pencil icon to edit.
2. Add one line per gallery, in this order: name, city, area, default venue, the web address of its exhibitions page, notes.
3. Click **Commit changes**. It will be included in the next morning's run.

Tip: use the page that lists current exhibitions, not the homepage, unless the homepage shows them.

---

## The rules this program follows

- It checks each site's robots.txt (the file where sites say what bots may visit) and skips sites that say no.
- It waits 5 seconds between sites and visits each page once a day.
- It identifies itself with your contact email.
- It keeps facts only: title, dates, venue, price and a link back. It never copies descriptions or images.
- Claude is told never to guess. Anything not written on the page is left blank and flagged.
- Nothing is published automatically. You check every row marked "needs review".

Before using this at scale, ask a lawyer to review the approach under Indian law. This guide is not legal advice.

---

## Try it without the internet (optional)

If you have Python on your computer, open a terminal in this folder and run:

```
pip install -r requirements.txt
python collect.py --sources tests/practice_sources.csv --offline tests/pages --today 2026-10-28
python -m unittest discover tests
```

This uses three made-up gallery pages in `tests/pages`. Nothing real is visited.

---

## What it can't do yet

- Read Instagram. Instagram doesn't allow this; use pasted links on the website instead.
- Read pages that only load their content with JavaScript. If a gallery's row says ok but finds 0 events, that may be why. Tell Claude in our chat and we'll add a fix.
- Follow links to each show's own page. It reads only the page listed in `sources.csv`.
- Publish to the website. That comes once the website exists.

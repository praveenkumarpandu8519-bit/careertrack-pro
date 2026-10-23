# CareerTrack Pro — Free Automatic Job Finder (starter)

This version keeps the current HTML dashboard and adds a scheduled collector that can retrieve public vacancies from configured employer job boards.

## What it does
- Collects public postings from configured Greenhouse, Lever, and Ashby boards.
- Filters likely matches for Clinical Data Management, Clinical Research, Pharmacovigilance, Regulatory Affairs, and Medical Coding.
- Stores the generated feed in `data/jobs.json`.
- Runs every six hours through GitHub Actions after you publish this repository.
- Preserves old listings and application statuses when a feed refreshes.
- The dashboard attempts to load `data/jobs.json` on open.

## Important coverage limitation
This is a connector framework, not a magical feed for every company. Each employer needs a real public board token/site name in `collector/companies.json`. The initial config intentionally has blank placeholders rather than invented tokens. A company may use another ATS or restrict access and will need a separate permitted connector. No scraper can guarantee every newly published job.

## Set it up for free

1. Create a GitHub account if you do not already have one.
2. Create a new repository named `careertrack-pro` (a private repository is fine for the collector; for GitHub Pages, check the current plan's Pages availability for private repositories).
3. Upload the contents of this folder to the repository root. Do not upload the parent folder as an extra nested folder.
4. In GitHub, open **Actions** and enable workflows if prompted.
5. Open **Actions → Update CareerTrack Pro jobs → Run workflow** to test the collector.
6. Add real employer board entries to `collector/companies.json`, commit the change, then run the workflow again.
7. For a public static dashboard, enable GitHub Pages in repository settings and use the deployed page URL. The scheduled workflow writes the new `data/jobs.json` to the repository, which Pages can serve.
8. For local testing, run `python collector/collect_jobs.py` from the project root and use VS Code Live Server. If the browser opened the page as a `file://` URL, use Live Server instead.

## Add a company

The public careers page usually reveals the platform and board token:
- Greenhouse: `https://boards.greenhouse.io/<board_token>`; API reads `https://boards-api.greenhouse.io/v1/boards/<board_token>/jobs?content=true`
- Lever: `https://jobs.lever.co/<site_name>`; API reads `https://api.lever.co/v0/postings/<site_name>?mode=json`
- Ashby: `https://jobs.ashbyhq.com/<job_board_name>`; API reads `https://api.ashbyhq.com/posting-api/job-board/<job_board_name>`

Add an object such as:
```json
{"company":"Example Bio","platform":"greenhouse","board_token":"real-board-token","category":"biotech"}
```
Use a real token copied from the employer's public job board URL; don't guess. Repeat for each company.

## Current limitations / next stages
- This starter does not yet send email/push notifications.
- It cannot promise coverage of all MNCs, CROs, and startups until the employer directory is populated.
- Source dates can represent publication or last update depending on ATS.
- Jobs are retained when missing from later snapshots; manually verify vacancy status before applying.
- Do not commit personal credentials or API secrets into the repository.
- The dashboard's application statuses remain browser-local; cross-device syncing requires a database/auth layer.

## Official source docs
- Greenhouse public Job Board API: https://docs.greenhouse.io/job-board
- Lever Postings API: https://github.com/lever/postings-api
- Ashby public job postings API: https://developers.ashbyhq.com/docs/public-job-posting-api
- GitHub Actions scheduled workflows: https://docs.github.com/actions/using-workflows/events-that-trigger-workflows#schedule

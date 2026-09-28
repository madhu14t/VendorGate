# VendorGate: vendor onboarding review (Zamp ASA case study, PS-2)

Takes a vendor submission, runs it through a 5-stage validation pipeline, and returns
**approved / pending / rejected** with visible reasoning and a drafted message back to the vendor.

## Stack
| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11 + Flask | Small, fast to deploy, streams events live |
| Live run view | NDJSON streaming over `fetch` | Each check appears as it executes, no websockets needed |
| Storage | SQLite | Run history for the dashboard, zero setup |
| LLM | **Groq** (`llama-3.3-70b-versatile`) | Very fast inference, so live demos feel instant |
| Frontend | Vanilla HTML/CSS/JS (single file) | No build step, nothing to break |
| Hosting | Render (free tier) | Public link in about 5 minutes |

## Design principle
**Decisions are deterministic code. Language is AI.** Approve / pending / reject comes from explicit rules
(`engine.py > decide`). Groq is used only for (1) judging gray-zone name matches and
(2) writing the summary and vendor email. If Groq is down or the key is missing, the app falls back to
deterministic logic and templates, so a demo never breaks.

## Pipeline
1. **Intake**: normalise fields
2. **Completeness**: required fields and documents, per country
3. **Format validation**: GSTIN checksum, PAN, IFSC, EIN, VAT, UEN, IBAN mod-97, ABA routing checksum
4. **Cross-reference**: company name vs account holder (AI for gray zone), GSTIN vs PAN, PAN type vs entity type, document names, bank vs company country, email domain
5. **Risk and policy**: sanctions, high-risk jurisdictions, duplicate tax ID / bank account vs vendor master
6. **Decision**: any critical check → rejected; 3+ independent fraud signals → rejected; any failed check → pending; risk score ≥ 40 → pending; else approved
7. **Communication**: plain-English summary + email (pending: lists exactly what's missing; rejected: courteous and generic, never reveals fraud logic)

## Scenarios (the 1 happy path + 7 edge cases)
| Scenario | Expected |
|---|---|
| Happy path | approved |
| Incomplete + typos (missing docs, bad IFSC, GSTIN checksum) | pending + precise ask |
| Abbreviated bank name (gray zone → Groq judges) | approved with warning |
| Payment-fraud pattern (personal account + personal PAN + free email) | rejected (3 signals) |
| Tax ID for wrong country (US EIN for a German company) | pending |
| W-9 issued in a different name | pending |
| Duplicate tax ID of an existing vendor | rejected |
| Sanctioned jurisdiction | rejected |

## Run locally
```bash
pip install -r requirements.txt
export GROQ_API_KEY=your_key      # optional; works without it (fallback mode)
python app.py                      # http://localhost:5000
```

## Deploy (Render)
1. Push this folder to a GitHub repo.
2. Render → New → Web Service → connect the repo (it reads `render.yaml`).
3. Add env var `GROQ_API_KEY`. Deploy. Open `/health` to confirm `"groq": true`.
4. Free tier sleeps after inactivity: open the link once before the interview. Run history resets on redeploy (SQLite on ephemeral disk); use **Run all scenarios** on the dashboard to refill it.

## Assumptions (mention these in the interview)
- Documents are represented by type + the name printed on them (stands in for OCR/extraction, which is out of scope for this problem).
- The vendor master is a seeded snapshot (3 vendors) standing in for the ERP.
- Sanctions and high-risk lists are illustrative, not a real screening provider.
- Rejected vendors get a generic email. Specifics go to compliance internally.
- Thresholds (name match 90%, gray zone 60–90%, risk 40, 3 fraud signals) are policy knobs in `engine.py`.

## What I'd build next
Real document extraction (OCR + LLM), live sanctions API, vendor self-serve resubmission link that re-runs only failed checks, approval queue with human override and audit log, Postgres.

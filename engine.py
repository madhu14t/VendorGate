"""
VendorGate pipeline engine.

Design principle: DECISIONS ARE DETERMINISTIC CODE, LANGUAGE IS AI.
  - Every approve / pending / reject outcome comes from plain rules below.
  - Groq (LLM) is used for exactly two things:
      1. Judging "gray zone" name matches (e.g. "Orion Packaging Sols" vs "Orion Packaging Solutions Pvt Ltd")
      2. Writing the plain-English summary + the message back to the vendor
  - If Groq is unavailable, both fall back to deterministic logic/templates, so the demo never breaks.
"""
import os
import re
import json
import time
import difflib
import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ----------------------------------------------------------------------------
# Reference data / policy (this is the "procurement system" side of the problem)
# ----------------------------------------------------------------------------
SANCTIONED = {"IR": "Iran", "KP": "North Korea", "SY": "Syria", "CU": "Cuba"}
HIGH_RISK = {"NG": "Nigeria", "PK": "Pakistan", "MM": "Myanmar", "AF": "Afghanistan", "YE": "Yemen", "LY": "Libya"}
FREE_EMAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me", "protonmail.com",
              "rediffmail.com", "icloud.com", "aol.com"}

DOC_LABELS = {
    "certificate_of_incorporation": "Certificate of incorporation",
    "gst_certificate": "GST registration certificate",
    "pan_card": "PAN card",
    "cancelled_cheque": "Cancelled cheque",
    "w9": "IRS Form W-9",
    "bank_letter": "Bank confirmation letter",
    "commercial_register_extract": "Commercial register extract",
    "vat_certificate": "VAT registration certificate",
    "acra_certificate": "ACRA business profile",
    "registration_certificate": "Business registration certificate",
}

REQUIRED_DOCS = {
    "IN": ["certificate_of_incorporation", "gst_certificate", "pan_card", "cancelled_cheque"],
    "US": ["w9", "bank_letter"],
    "GB": ["certificate_of_incorporation", "bank_letter"],
    "DE": ["commercial_register_extract", "vat_certificate", "bank_letter"],
    "SG": ["acra_certificate", "bank_letter"],
}
DEFAULT_DOCS = ["registration_certificate", "bank_letter"]
BANK_DOCS = {"cancelled_cheque", "bank_letter"}  # these should carry the account holder's name

BASE_REQUIRED = {
    "legal_name": "Legal company name",
    "country": "Country of registration",
    "entity_type": "Entity type",
    "tax_id": "Tax registration number",
    "address": "Registered address",
    "contact_name": "Contact person",
    "contact_email": "Contact email",
    "bank_account_holder": "Bank account holder name",
    "bank_name": "Bank name",
    "bank_country": "Bank country",
}
COUNTRY_REQUIRED = {
    "IN": {"pan": "PAN", "ifsc": "IFSC code", "account_number": "Bank account number"},
    "US": {"routing_number": "ABA routing number", "account_number": "Bank account number"},
    "GB": {"iban": "IBAN"},
    "DE": {"iban": "IBAN"},
}

# Vendor master snapshot (stands in for the ERP / procurement system)
VENDOR_MASTER = [
    {"name": "Acme Industrial Supplies Pvt Ltd", "tax_id": "07AABCA9999Q1ZK", "account": "123456789012"},
    {"name": "Brightwave Software Inc", "tax_id": "12-3456789", "account": "000123456789"},
    {"name": "Nordlicht GmbH", "tax_id": "DE123456789", "account": "DE29100100100987654321"},
]

# ----------------------------------------------------------------------------
# Small validators
# ----------------------------------------------------------------------------
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_valid(g):
    if not re.fullmatch(r"[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g or ""):
        return False, "does not match the 15-character GSTIN pattern"
    total = 0
    for i, ch in enumerate(g[:14]):
        v = _B36.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    if _B36[(36 - total % 36) % 36] != g[14]:
        return False, "checksum digit is wrong (likely a typo)"
    return True, "valid GSTIN structure and checksum"


def iban_valid(iban):
    iban = (iban or "").replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}", iban):
        return False
    moved = iban[4:] + iban[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def routing_valid(r):
    if not re.fullmatch(r"\d{9}", r or ""):
        return False
    d = [int(x) for x in r]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


TAX_PATTERNS = {
    "IN": ("GSTIN (15 characters, e.g. 27AABCN1234C1Z2)", None),  # custom checksum validation
    "US": ("EIN (NN-NNNNNNN)", r"\d{2}-\d{7}"),
    "GB": ("UK VAT number (GB + 9 digits)", r"GB\d{9}"),
    "DE": ("German VAT ID (DE + 9 digits)", r"DE\d{9}"),
    "SG": ("Singapore UEN (e.g. 201912345K)", r"\d{8,9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z]"),
}


def guess_taxid_country(t):
    for cc, (_, pat) in TAX_PATTERNS.items():
        if cc == "IN":
            if re.fullmatch(r"[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", t):
                return cc
        elif pat and re.fullmatch(pat, t):
            return cc
    return None


_SUFFIX = {"pvt", "private", "ltd", "limited", "llp", "llc", "inc", "incorporated", "corp", "corporation",
           "gmbh", "co", "company", "plc", "pte", "ag", "sa", "the"}


def norm_name(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = [t for t in s.split() if t not in _SUFFIX]
    return " ".join(toks)


def name_sim(a, b):
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    r1 = difflib.SequenceMatcher(None, na, nb).ratio()
    r2 = difflib.SequenceMatcher(None, " ".join(sorted(na.split())), " ".join(sorted(nb.split()))).ratio()
    return max(r1, r2)


# ----------------------------------------------------------------------------
# Groq helper
# ----------------------------------------------------------------------------
def groq_chat(messages, json_mode=False, timeout=20):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    body = {"model": GROQ_MODEL, "messages": messages, "temperature": 0.2, "max_tokens": 700}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:  # never let the LLM break the pipeline
        print("Groq error:", e)
        return None


def llm_same_entity(a, b):
    """Gray-zone helper: are these two names the same legal entity? Returns (bool|None, reason)."""
    out = groq_chat([
        {"role": "system", "content": "You are a KYC analyst. Decide whether two names refer to the same legal entity "
                                       "(allow abbreviations, word order, legal-suffix differences and minor typos; "
                                       "do NOT allow different people or different businesses). "
                                       "Reply ONLY with JSON: {\"same_entity\": true|false, \"reason\": \"<max 20 words>\"}"},
        {"role": "user", "content": json.dumps({"name_a": a, "name_b": b})},
    ], json_mode=True)
    if not out:
        return None, ""
    try:
        d = json.loads(out)
        return bool(d.get("same_entity")), str(d.get("reason", ""))[:160]
    except Exception:
        return None, ""


# ----------------------------------------------------------------------------
# Check helper
# ----------------------------------------------------------------------------
def C(name, status, detail, fix=None, fraud=False, ai=False):
    """status: pass | info | warn | fail | critical"""
    return {"name": name, "status": status, "detail": detail, "fix": fix, "fraud": fraud, "ai": ai}


# ----------------------------------------------------------------------------
# Stages
# ----------------------------------------------------------------------------
def stage_intake(sub):
    for k, v in list(sub.items()):
        if isinstance(v, str):
            sub[k] = v.strip()
    sub["country"] = sub.get("country", "").upper()
    sub["bank_country"] = sub.get("bank_country", "").upper()
    for k in ("tax_id", "pan", "ifsc", "iban"):
        if sub.get(k):
            sub[k] = re.sub(r"\s+", "", sub[k]).upper()
    sub["documents"] = [d for d in sub.get("documents", []) if d.get("type")]
    n_filled = sum(1 for k, v in sub.items() if k != "documents" and v)
    return [
        C("Submission received", "pass", f"{sub.get('legal_name') or 'Unnamed vendor'} · {sub.get('country') or '?'} · "
                                         f"{n_filled} fields, {len(sub['documents'])} documents"),
        C("Normalised fields", "info", "Trimmed whitespace, upper-cased IDs and country codes"),
    ]


def stage_completeness(sub):
    out = []
    cc = sub.get("country", "")
    missing = []
    req = dict(BASE_REQUIRED)
    req.update(COUNTRY_REQUIRED.get(cc, {}))
    for k, label in req.items():
        if not sub.get(k):
            missing.append(label)
            out.append(C(f"Field: {label}", "fail", "Missing", fix=f"Provide your {label.lower()}"))
    if not missing:
        out.append(C("Required fields", "pass", f"All {len(req)} required fields for {cc or 'this country'} are present"))
    needed = REQUIRED_DOCS.get(cc, DEFAULT_DOCS)
    have = {d["type"] for d in sub["documents"]}
    miss_docs = [d for d in needed if d not in have]
    for d in miss_docs:
        out.append(C(f"Document: {DOC_LABELS[d]}", "fail", "Not attached", fix=f"Upload: {DOC_LABELS[d]}"))
    if not miss_docs:
        out.append(C("Required documents", "pass", f"All {len(needed)} required documents attached"))
    return out


def stage_format(sub):
    out = []
    cc, tax = sub.get("country", ""), sub.get("tax_id", "")
    # tax id
    if tax:
        if cc == "IN":
            ok, why = gstin_valid(tax)
            if ok:
                out.append(C("Tax ID format", "pass", f"{tax} — {why}"))
            else:
                hint = guess_taxid_country(tax)
                extra = f" It looks like a {hint} tax ID instead." if hint else ""
                out.append(C("Tax ID format", "fail", f"{tax} — {why}.{extra}",
                             fix="Provide a valid GSTIN (15 characters)"))
        elif cc in TAX_PATTERNS:
            label, pat = TAX_PATTERNS[cc]
            if re.fullmatch(pat, tax):
                out.append(C("Tax ID format", "pass", f"{tax} — matches {label}"))
            else:
                hint = guess_taxid_country(tax)
                extra = f" It matches the {TAX_PATTERNS[hint][0]} format, not {cc}." if hint else ""
                out.append(C("Tax ID format", "fail", f"{tax} — expected {label}.{extra}",
                             fix=f"Provide a valid {label}"))
        else:
            out.append(C("Tax ID format", "info", f"No format rule for country '{cc}' — routed to manual verification"))
    # PAN (India)
    if cc == "IN" and sub.get("pan"):
        if re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", sub["pan"]):
            out.append(C("PAN format", "pass", f"{sub['pan']} — valid structure"))
        else:
            out.append(C("PAN format", "fail", f"{sub['pan']} — expected 5 letters, 4 digits, 1 letter",
                         fix="Provide a valid 10-character PAN"))
    # bank
    if cc == "IN" and sub.get("ifsc"):
        if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", sub["ifsc"]):
            out.append(C("IFSC format", "pass", f"{sub['ifsc']} — valid"))
        else:
            out.append(C("IFSC format", "fail", f"{sub['ifsc']} — must be 4 letters, a zero, then 6 characters",
                         fix="Provide a valid 11-character IFSC code"))
    if cc == "IN" and sub.get("account_number"):
        if re.fullmatch(r"\d{9,18}", sub["account_number"]):
            out.append(C("Account number", "pass", "9–18 digits"))
        else:
            out.append(C("Account number", "fail", "Indian account numbers are 9–18 digits",
                         fix="Provide a valid bank account number"))
    if cc == "US" and sub.get("routing_number"):
        if routing_valid(sub["routing_number"]):
            out.append(C("ABA routing number", "pass", "Checksum valid"))
        else:
            out.append(C("ABA routing number", "fail", "Not a valid 9-digit ABA routing number",
                         fix="Provide a valid ABA routing number"))
    if sub.get("iban"):
        if iban_valid(sub["iban"]):
            out.append(C("IBAN", "pass", "Mod-97 checksum valid"))
        else:
            out.append(C("IBAN", "fail", f"{sub['iban']} fails the mod-97 checksum", fix="Provide a valid IBAN"))
    # email
    if sub.get("contact_email"):
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", sub["contact_email"]):
            out.append(C("Contact email", "pass", "Valid format"))
        else:
            out.append(C("Contact email", "fail", "Not a valid email address", fix="Provide a valid contact email"))
    return out


def stage_crossref(sub):
    out = []
    cc = sub.get("country", "")
    legal, holder = sub.get("legal_name", ""), sub.get("bank_account_holder", "")

    # 1. Company name vs bank account holder (the classic payment-fraud check)
    if legal and holder:
        s = name_sim(legal, holder)
        if s >= 0.9:
            out.append(C("Company name ↔ account holder", "pass", f"Match ({s:.0%} similar after normalising legal suffixes)"))
        elif s >= 0.6:
            same, why = llm_same_entity(legal, holder)
            if same is True:
                out.append(C("Company name ↔ account holder", "warn",
                             f"Close but not identical ({s:.0%}). AI judged same entity: {why}", ai=True))
            elif same is False:
                out.append(C("Company name ↔ account holder", "fail",
                             f"'{legal}' vs '{holder}' ({s:.0%}). AI judged different entities: {why}",
                             fix="Provide a bank letter showing the account is held in the registered company name",
                             fraud=True, ai=True))
            else:  # no LLM available
                lvl = "warn" if s >= 0.7 else "fail"
                out.append(C("Company name ↔ account holder", lvl,
                             f"'{legal}' vs '{holder}' ({s:.0%} similar) — needs confirmation",
                             fix="Confirm the account holder name with a bank letter" if lvl == "fail" else None,
                             fraud=lvl == "fail"))
        else:
            out.append(C("Company name ↔ account holder", "fail",
                         f"'{legal}' vs '{holder}' — completely different names ({s:.0%})",
                         fix="Provide a bank letter showing the account is held in the registered company name",
                         fraud=True))

    # 2. Bank country vs company country
    if sub.get("bank_country") and cc:
        if sub["bank_country"] == cc:
            out.append(C("Bank country ↔ company country", "pass", f"Both {cc}"))
        else:
            out.append(C("Bank country ↔ company country", "warn",
                         f"Company in {cc}, bank in {sub['bank_country']} — offshore account", fraud=True))
    if sub.get("iban") and sub.get("bank_country") and sub["iban"][:2] != sub["bank_country"]:
        out.append(C("IBAN prefix ↔ bank country", "fail",
                     f"IBAN starts with {sub['iban'][:2]} but bank country is {sub['bank_country']}",
                     fix="Confirm bank country and IBAN"))

    # 3. India-specific identity consistency
    if cc == "IN" and sub.get("tax_id") and sub.get("pan"):
        ok, _ = gstin_valid(sub["tax_id"])
        if ok:
            if sub["tax_id"][2:12] == sub["pan"]:
                out.append(C("GSTIN ↔ PAN", "pass", "PAN embedded in the GSTIN matches the PAN provided"))
            else:
                out.append(C("GSTIN ↔ PAN", "fail", f"GSTIN embeds {sub['tax_id'][2:12]} but PAN given is {sub['pan']}",
                             fix="Provide the PAN that matches your GSTIN", fraud=True))
        if re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", sub["pan"]):
            expected = {"company": "C", "partnership": "F", "llp": "F", "individual": "P"}.get(
                (sub.get("entity_type") or "").lower())
            actual = sub["pan"][3]
            if expected:
                if actual == expected:
                    out.append(C("PAN type ↔ entity type", "pass", f"PAN 4th letter '{actual}' fits a {sub['entity_type']}"))
                else:
                    out.append(C("PAN type ↔ entity type", "fail",
                                 f"PAN 4th letter is '{actual}' (=" + {"C": "company", "F": "firm/LLP", "P": "individual"}.get(actual, "other")
                                 + f") but vendor claims to be a {sub['entity_type']}",
                                 fix="Provide the PAN issued to the registered business entity", fraud=True))

    # 4. Documents carry the right names
    doc_issues, doc_ok = [], 0
    for d in sub["documents"]:
        ent = (d.get("entity_name") or "").strip()
        target = holder if d["type"] in BANK_DOCS else legal
        if not ent:
            doc_issues.append(f"{DOC_LABELS.get(d['type'], d['type'])}: name could not be read")
            continue
        if name_sim(ent, target) >= 0.85:
            doc_ok += 1
        else:
            doc_issues.append(f"{DOC_LABELS.get(d['type'], d['type'])} is in the name '{ent}', expected '{target}'")
    if sub["documents"]:
        if doc_issues:
            out.append(C("Document names", "fail", "; ".join(doc_issues),
                         fix="Upload documents issued in the registered company name"))
        else:
            out.append(C("Document names", "pass", f"All {doc_ok} documents carry the expected name"))

    # 5. Email domain
    em = sub.get("contact_email", "")
    if "@" in em:
        dom = em.split("@")[1].lower()
        if dom in FREE_EMAIL:
            out.append(C("Contact email domain", "warn", f"Company uses a free mailbox ({dom})", fraud=True))
        else:
            site = re.sub(r"^https?://(www\.)?", "", (sub.get("website") or "").lower()).split("/")[0]
            if site and site != dom and not site.endswith("." + dom) and not dom.endswith("." + site):
                out.append(C("Contact email domain", "warn", f"Email domain {dom} differs from website {site}"))
            else:
                out.append(C("Contact email domain", "pass", f"Corporate domain ({dom})"))
    return out


def stage_risk(sub):
    out = []
    cc, bc = sub.get("country", ""), sub.get("bank_country", "")
    hit = [c for c in (cc, bc) if c in SANCTIONED]
    if hit:
        out.append(C("Sanctions screening", "critical",
                     f"{SANCTIONED[hit[0]]} is on the sanctioned-jurisdiction list — onboarding is prohibited"))
    else:
        out.append(C("Sanctions screening", "pass", "No sanctioned jurisdictions involved"))
    hr = [c for c in (cc, bc) if c in HIGH_RISK]
    if hr:
        out.append(C("Jurisdiction risk", "warn", f"{HIGH_RISK[hr[0]]} is a high-risk jurisdiction — enhanced due diligence"))
    else:
        out.append(C("Jurisdiction risk", "pass", "Standard-risk jurisdiction"))

    # duplicate detection against the vendor master
    dup_tax = next((v for v in VENDOR_MASTER if sub.get("tax_id") and v["tax_id"] == sub["tax_id"]), None)
    if dup_tax:
        if name_sim(dup_tax["name"], sub.get("legal_name", "")) >= 0.9:
            out.append(C("Duplicate check: tax ID", "info", f"Existing vendor record found ({dup_tax['name']}) — treated as an update"))
        else:
            out.append(C("Duplicate check: tax ID", "critical",
                         f"Tax ID {sub['tax_id']} is already registered to '{dup_tax['name']}' — possible impersonation"))
    else:
        out.append(C("Duplicate check: tax ID", "pass", "Not found in vendor master"))
    acct = sub.get("iban") or sub.get("account_number")
    dup_acct = next((v for v in VENDOR_MASTER if acct and v["account"] == acct), None)
    if dup_acct and name_sim(dup_acct["name"], sub.get("legal_name", "")) < 0.9:
        out.append(C("Duplicate check: bank account", "critical",
                     f"Bank account already belongs to '{dup_acct['name']}'"))
    else:
        out.append(C("Duplicate check: bank account", "pass", "Bank account not used by another vendor"))
    return out


WEIGHT = {"pass": 0, "info": 0, "warn": 8, "fail": 20, "critical": 100}


def decide(all_checks):
    fraud = sum(1 for c in all_checks if c["fraud"] and c["status"] in ("warn", "fail"))
    score = sum(WEIGHT[c["status"]] for c in all_checks) + 12 * fraud
    score = min(score, 100)
    crit = [c for c in all_checks if c["status"] == "critical"]
    fails = [c for c in all_checks if c["status"] == "fail"]
    warns = [c for c in all_checks if c["status"] == "warn"]
    if crit:
        status, rule = "rejected", "Hard-stop rule: a critical policy check failed"
    elif fraud >= 3:
        status, rule = "rejected", f"Fraud-pattern rule: {fraud} independent fraud signals on one submission"
    elif fails:
        status, rule = "pending", f"{len(fails)} issue(s) must be fixed by the vendor before approval"
    elif score >= 40:
        status, rule = "pending", "No hard failures, but combined risk score is too high for auto-approval"
    else:
        status, rule = "approved", "All checks passed and risk score is below the auto-approval threshold (40)"
    reasons = [f"{c['name']}: {c['detail']}" for c in crit + fails + warns]
    return {
        "status": status,
        "risk_score": score,
        "rule": rule,
        "fraud_signals": fraud,
        "reasons": reasons,
        "missing_items": [c["fix"] for c in fails if c.get("fix")] if status == "pending" else [],
    }


# ----------------------------------------------------------------------------
# Communication (Groq, with deterministic fallback)
# ----------------------------------------------------------------------------
def fallback_comms(sub, d):
    name, contact = sub.get("legal_name") or "your company", sub.get("contact_name") or "there"
    if d["status"] == "approved":
        summary = f"{name} passed every check (risk {d['risk_score']}/100) and can be activated for payments."
        subj = f"Welcome aboard — {name} is approved"
        body = (f"Hi {contact},\n\nThanks for completing onboarding. {name} has been approved as a vendor. "
                "You'll receive purchase orders from our team shortly, and invoices can be sent to accounts payable.\n\nBest regards,\nProcurement Team")
    elif d["status"] == "pending":
        summary = (f"{name} can't be approved yet: {len(d['missing_items'])} item(s) need fixing"
                   if d["missing_items"] else f"{name} needs a manual review before approval") + f" (risk {d['risk_score']}/100)."
        items = "\n".join(f"  • {m}" for m in d["missing_items"]) or "  • Our team will contact you for a short verification call"
        subj = f"Action needed to finish onboarding {name}"
        body = (f"Hi {contact},\n\nThanks for submitting your details. To complete onboarding we still need:\n\n{items}\n\n"
                "Reply with the updated details or documents and we'll review them right away.\n\nBest regards,\nProcurement Team")
    else:
        summary = f"{name} was rejected: {d['rule'].lower()}. Escalate to compliance; do not contact the vendor with specifics."
        subj = f"Update on your vendor application — {name}"
        body = (f"Hi {contact},\n\nThank you for your interest in working with us. After review, we're unable to proceed with "
                "your vendor application at this time.\n\nBest regards,\nProcurement Team")
    return {"summary": summary, "email_subject": subj, "email_body": body}


def stage_comms(sub, d):
    fb = fallback_comms(sub, d)
    payload = {
        "vendor": sub.get("legal_name"), "contact_name": sub.get("contact_name"), "country": sub.get("country"),
        "status": d["status"], "risk_score": d["risk_score"], "rule": d["rule"],
        "internal_reasons": d["reasons"], "items_vendor_must_fix": d["missing_items"],
    }
    system = (
        "You write vendor-onboarding communications for a procurement team. Return ONLY JSON with keys "
        "summary, email_subject, email_body.\n"
        "- summary: 2 plain-English sentences for a non-technical procurement manager explaining the decision and the main reason.\n"
        "- email_body: short, polite, professional email to the vendor contact, signed 'Procurement Team'.\n"
        "- If status is 'pending': list EXACTLY the items in items_vendor_must_fix as bullets (use '• '). Do not invent extra items. If the list is empty, say our team will contact them for verification.\n"
        "- If status is 'rejected': be courteous and generic. NEVER mention fraud, sanctions, duplicates or internal checks.\n"
        "- If status is 'approved': welcome them and say purchase orders will follow.\n"
        "Never state the risk score in the email."
    )
    out = groq_chat([{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload)}], json_mode=True)
    if out:
        try:
            j = json.loads(out)
            if all(k in j and isinstance(j[k], str) and j[k].strip() for k in ("summary", "email_subject", "email_body")):
                return {**{k: j[k] for k in ("summary", "email_subject", "email_body")}, "generated_by": f"Groq · {GROQ_MODEL}"}
        except Exception:
            pass
    return {**fb, "generated_by": "Template fallback (no Groq response)"}


# ----------------------------------------------------------------------------
# Orchestrator — yields events the UI renders live
# ----------------------------------------------------------------------------
STAGES = [
    ("intake", "Intake", "Receive and normalise the submission", stage_intake),
    ("completeness", "Completeness", "Required fields and documents for this country", stage_completeness),
    ("format", "Format validation", "Tax ID, bank and contact formats (with checksums)", stage_format),
    ("crossref", "Cross-reference", "Do the fields and documents agree with each other?", stage_crossref),
    ("risk", "Risk and policy", "Sanctions, jurisdiction and duplicate detection", stage_risk),
]


def run_pipeline(sub, fast=False):
    pause = 0 if fast else 0.45
    sub = json.loads(json.dumps(sub))  # deep copy
    all_checks = []
    for sid, title, desc, fn in STAGES:
        yield {"type": "stage_start", "stage": sid, "title": title, "desc": desc}
        time.sleep(pause)
        checks = fn(sub)
        for c in checks:
            all_checks.append(c)
            yield {"type": "check", "stage": sid, **c}
            time.sleep(pause * 0.6)
        worst = "pass"
        for lvl in ("critical", "fail", "warn"):
            if any(c["status"] == lvl for c in checks):
                worst = lvl
                break
        yield {"type": "stage_end", "stage": sid, "status": worst}

    yield {"type": "stage_start", "stage": "decision", "title": "Decision", "desc": "Apply the approval rules to the findings"}
    time.sleep(pause)
    d = decide(all_checks)
    yield {"type": "stage_end", "stage": "decision", "status": {"approved": "pass", "pending": "warn", "rejected": "critical"}[d["status"]]}
    yield {"type": "decision", **d}

    yield {"type": "stage_start", "stage": "comms", "title": "Communication",
           "desc": "Draft the summary and vendor message (Groq)"}
    comms = stage_comms(sub, d)
    yield {"type": "stage_end", "stage": "comms", "status": "pass"}
    yield {"type": "comms", **comms}

"""Demo scenarios: 1 happy path + 7 edge cases. Each is a realistic vendor submission."""


def docs(pairs):
    return [{"type": t, "entity_name": n} for t, n in pairs]


SAMPLES = [
    {
        "id": "happy",
        "label": "Happy path",
        "blurb": "Clean Indian vendor — everything consistent. Expect: approved.",
        "data": {
            "legal_name": "Nimbus Logistics Private Limited", "country": "IN", "entity_type": "company",
            "tax_id": "27AABCN1234C1Z2", "pan": "AABCN1234C",
            "address": "Plot 14, MIDC Andheri East, Mumbai 400093",
            "contact_name": "Priya Nair", "contact_email": "priya.nair@nimbuslogistics.in", "website": "nimbuslogistics.in",
            "bank_account_holder": "NIMBUS LOGISTICS PVT LTD", "bank_name": "HDFC Bank", "bank_country": "IN",
            "account_number": "50100234567891", "ifsc": "HDFC0001234",
            "documents": docs([
                ("certificate_of_incorporation", "Nimbus Logistics Private Limited"),
                ("gst_certificate", "Nimbus Logistics Private Limited"),
                ("pan_card", "Nimbus Logistics Private Limited"),
                ("cancelled_cheque", "NIMBUS LOGISTICS PVT LTD"),
            ]),
        },
    },
    {
        "id": "incomplete",
        "label": "Incomplete + typos",
        "blurb": "Missing documents, bad IFSC and GSTIN checksum. Expect: pending + a precise ask email.",
        "data": {
            "legal_name": "Kaveri Textiles LLP", "country": "IN", "entity_type": "llp",
            "tax_id": "29AAFFK5678L1ZX", "pan": "AAFFK5678L",
            "address": "12 Race Course Road, Bengaluru 560001",
            "contact_name": "Suresh Iyer", "contact_email": "accounts@kaveritextiles.com", "website": "kaveritextiles.com",
            "bank_account_holder": "Kaveri Textiles LLP", "bank_name": "ICICI Bank", "bank_country": "IN",
            "account_number": "0012345678", "ifsc": "ICIC001234",
            "documents": docs([
                ("certificate_of_incorporation", "Kaveri Textiles LLP"),
                ("gst_certificate", "Kaveri Textiles LLP"),
            ]),
        },
    },
    {
        "id": "fuzzy_name",
        "label": "Abbreviated bank name",
        "blurb": "Account holder is an abbreviation of the company name. AI judges the gray zone. Expect: approved with a warning.",
        "data": {
            "legal_name": "Orion Packaging Solutions Pvt Ltd", "country": "IN", "entity_type": "company",
            "tax_id": "33AABCO4321D1Z4", "pan": "AABCO4321D",
            "address": "SIDCO Industrial Estate, Guindy, Chennai 600032",
            "contact_name": "Ramesh Kumar", "contact_email": "ramesh@orionpack.in", "website": "orionpack.in",
            "bank_account_holder": "Orion Pack Sol", "bank_name": "State Bank of India", "bank_country": "IN",
            "account_number": "38291045567", "ifsc": "SBIN0001234",
            "documents": docs([
                ("certificate_of_incorporation", "Orion Packaging Solutions Pvt Ltd"),
                ("gst_certificate", "Orion Packaging Solutions Pvt Ltd"),
                ("pan_card", "Orion Packaging Solutions Pvt Ltd"),
                ("cancelled_cheque", "Orion Pack Sol"),
            ]),
        },
    },
    {
        "id": "fraud_pattern",
        "label": "Payment-fraud pattern",
        "blurb": "Company paid into a personal account, personal PAN, free email. Each alone is fixable; together they trigger a reject.",
        "data": {
            "legal_name": "Sterling Trade Links Pvt Ltd", "country": "IN", "entity_type": "company",
            "tax_id": "27AAPPM7788K1ZM", "pan": "AAPPM7788K",
            "address": "Office 3, Nariman Point, Mumbai 400021",
            "contact_name": "Rakesh Menon", "contact_email": "sterlingtradelinks@gmail.com", "website": "",
            "bank_account_holder": "Rakesh Menon", "bank_name": "Axis Bank", "bank_country": "IN",
            "account_number": "918020045612345", "ifsc": "UTIB0000123",
            "documents": docs([
                ("certificate_of_incorporation", "Sterling Trade Links Pvt Ltd"),
                ("gst_certificate", "Sterling Trade Links Pvt Ltd"),
                ("pan_card", "Rakesh Menon"),
                ("cancelled_cheque", "Rakesh Menon"),
            ]),
        },
    },
    {
        "id": "wrong_taxid",
        "label": "Tax ID for wrong country",
        "blurb": "German company supplied a US-style EIN. Expect: pending, asks for a German VAT ID.",
        "data": {
            "legal_name": "Rheinbach Engineering GmbH", "country": "DE", "entity_type": "company",
            "tax_id": "84-1234567", "pan": "",
            "address": "Hauptstrasse 45, 53359 Rheinbach",
            "contact_name": "Klaus Weber", "contact_email": "k.weber@rheinbach-eng.de", "website": "rheinbach-eng.de",
            "bank_account_holder": "Rheinbach Engineering GmbH", "bank_name": "Deutsche Bank", "bank_country": "DE",
            "iban": "DE89370400440532013000",
            "documents": docs([
                ("commercial_register_extract", "Rheinbach Engineering GmbH"),
                ("vat_certificate", "Rheinbach Engineering GmbH"),
                ("bank_letter", "Rheinbach Engineering GmbH"),
            ]),
        },
    },
    {
        "id": "doc_mismatch",
        "label": "W-9 in a different name",
        "blurb": "US vendor whose W-9 is under another business name. Expect: pending, asks for a matching document.",
        "data": {
            "legal_name": "Lumen Analytics LLC", "country": "US", "entity_type": "company",
            "tax_id": "47-1928374", "pan": "",
            "address": "500 Howard St, San Francisco, CA 94105",
            "contact_name": "Dana Whitfield", "contact_email": "dana@lumenanalytics.io", "website": "lumenanalytics.io",
            "bank_account_holder": "Lumen Analytics LLC", "bank_name": "Chase", "bank_country": "US",
            "routing_number": "021000021", "account_number": "4829105673",
            "documents": docs([
                ("w9", "Lumen Data Labs LLC"),
                ("bank_letter", "Lumen Analytics LLC"),
            ]),
        },
    },
    {
        "id": "duplicate",
        "label": "Duplicate tax ID",
        "blurb": "New vendor reuses the GSTIN of an existing vendor. Expect: rejected (possible impersonation).",
        "data": {
            "legal_name": "Zenith Office Supplies LLP", "country": "IN", "entity_type": "llp",
            "tax_id": "07AABCA9999Q1ZK", "pan": "AABCA9999Q",
            "address": "B-22 Nehru Place, New Delhi 110019",
            "contact_name": "Anil Kapoor", "contact_email": "anil@zenithoffice.in", "website": "zenithoffice.in",
            "bank_account_holder": "Zenith Office Supplies LLP", "bank_name": "Kotak Mahindra Bank", "bank_country": "IN",
            "account_number": "123456789012", "ifsc": "KKBK0000123",
            "documents": docs([
                ("certificate_of_incorporation", "Zenith Office Supplies LLP"),
                ("gst_certificate", "Zenith Office Supplies LLP"),
                ("pan_card", "Zenith Office Supplies LLP"),
                ("cancelled_cheque", "Zenith Office Supplies LLP"),
            ]),
        },
    },
    {
        "id": "sanctioned",
        "label": "Sanctioned jurisdiction",
        "blurb": "Vendor registered in a sanctioned country. Expect: rejected immediately, courteous generic email.",
        "data": {
            "legal_name": "Persepolis Trading Co", "country": "IR", "entity_type": "company",
            "tax_id": "10101234567", "pan": "",
            "address": "Valiasr St 210, Tehran",
            "contact_name": "Reza Karimi", "contact_email": "reza@persepolis-trading.com", "website": "persepolis-trading.com",
            "bank_account_holder": "Persepolis Trading Co", "bank_name": "Bank Melli", "bank_country": "IR",
            "iban": "", "account_number": "6037991234567890",
            "documents": docs([
                ("registration_certificate", "Persepolis Trading Co"),
                ("bank_letter", "Persepolis Trading Co"),
            ]),
        },
    },
]

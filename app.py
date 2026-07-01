#!/usr/bin/env python3
"""
Phone Number Verifier - Web App
---------------------------------
Simple web form: VA pastes leads (name, phone, one per line, comma-separated),
clicks Verify, sees results in a table, downloads a CSV.

Run locally:
    pip install flask requests --break-system-packages
    export TWILIO_ACCOUNT_SID=xxxx
    export TWILIO_AUTH_TOKEN=xxxx
    python app.py
    -> open http://localhost:5000

Deploy (so the VA can access it remotely, e.g. from the Philippines):
    See README.md for step-by-step Render.com deployment (free tier).
"""

import os
import io
import csv
import time
import difflib
import requests
from flask import Flask, request, render_template_string, send_file, session

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-key-change-me")

ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
LOOKUP_URL = "https://lookups.twilio.com/v2/PhoneNumbers/{number}"

# In-memory store of last results per session, keyed for CSV download
RESULTS_STORE = {}

PAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Phone Verifier</title>
<style>
  :root {
    --bg: #fafaf9;
    --card: #ffffff;
    --border: #e5e3df;
    --text: #2a2826;
    --muted: #79766f;
    --accent: #b35a3c;
    --accent-hover: #9a4c32;
    --call: #2e6b3e;
    --call-bg: #eaf4ec;
    --check: #9a6c12;
    --check-bg: #fdf3df;
    --skip: #a4373a;
    --skip-bg: #fbeaea;
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 40px 20px;
  }
  .wrap { max-width: 880px; margin: 0 auto; }
  h1 { font-size: 22px; font-weight: 600; margin-bottom: 4px; }
  p.sub { color: var(--muted); margin-top: 0; margin-bottom: 28px; font-size: 14px; }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 24px;
    margin-bottom: 20px;
  }
  label { display: block; font-weight: 600; font-size: 13px; margin-bottom: 8px; }
  .hint { color: var(--muted); font-size: 12px; margin-bottom: 10px; line-height: 1.5; }
  textarea {
    width: 100%;
    min-height: 220px;
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 13px;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    resize: vertical;
    background: #fdfdfc;
  }
  button {
    background: var(--accent);
    color: white;
    border: none;
    padding: 11px 22px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    margin-top: 14px;
  }
  button:hover { background: var(--accent-hover); }
  button.secondary {
    background: transparent;
    color: var(--accent);
    border: 1px solid var(--accent);
  }
  button.secondary:hover { background: #fbeee9; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }
  th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.02em; }
  .badge { display: inline-block; padding: 3px 9px; border-radius: 20px; font-size: 12px; font-weight: 600; }
  .badge-call { background: var(--call-bg); color: var(--call); }
  .badge-check { background: var(--check-bg); color: var(--check); }
  .badge-skip { background: var(--skip-bg); color: var(--skip); }
  .summary { display: flex; gap: 18px; margin-bottom: 18px; }
  .stat { flex: 1; border: 1px solid var(--border); border-radius: 8px; padding: 14px; text-align: center; }
  .stat .num { font-size: 24px; font-weight: 700; }
  .stat .label { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .error { background: var(--skip-bg); color: var(--skip); padding: 12px 16px; border-radius: 6px; font-size: 13px; margin-bottom: 16px; }
  .footer-note { font-size: 12px; color: var(--muted); margin-top: 30px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Phone Number Verifier</h1>
  <p class="sub">Paste this week's leads, check for bad numbers before research starts.</p>

  {% if error %}
    <div class="error">{{ error }}</div>
  {% endif %}

  <div class="card">
    <form method="POST" action="/verify">
      <label>Leads (one per line: Name, Phone)</label>
      <div class="hint">
        Example:<br>
        Jane Smith, (555) 123-4567<br>
        John Doe, 555-987-6543
      </div>
      <textarea name="leads" placeholder="Jane Smith, (555) 123-4567&#10;John Doe, 555-987-6543">{{ raw_input or '' }}</textarea>
      <button type="submit">Verify Numbers</button>
    </form>
  </div>

  {% if results %}
  <div class="card">
    <div class="summary">
      <div class="stat"><div class="num" style="color:var(--call)">{{ call_count }}</div><div class="label">Call</div></div>
      <div class="stat"><div class="num" style="color:var(--check)">{{ check_count }}</div><div class="label">Check Name</div></div>
      <div class="stat"><div class="num" style="color:var(--skip)">{{ skip_count }}</div><div class="label">Skip</div></div>
    </div>

    <form method="GET" action="/download">
      <button type="submit" class="secondary">Download CSV</button>
    </form>

    <table>
      <tr>
        <th>Name</th>
        <th>Phone</th>
        <th>Line Type</th>
        <th>Caller ID Name</th>
        <th>Verdict</th>
      </tr>
      {% for r in results %}
      <tr>
        <td>{{ r.name }}</td>
        <td>{{ r.phone }}</td>
        <td>{{ r.line_type or '-' }}</td>
        <td>{{ r.cnam_name or '-' }}</td>
        <td>
          {% if r.verdict == 'CALL' %}<span class="badge badge-call">CALL</span>
          {% elif r.verdict.startswith('CHECK') %}<span class="badge badge-check">CHECK NAME</span>
          {% else %}<span class="badge badge-skip">SKIP</span>{% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
  </div>
  {% endif %}

  <p class="footer-note">CALL = safe to start research. CHECK NAME = valid number but name on file doesn't match, do a quick confirmation call first. SKIP = invalid or disconnected, send back for a new number.</p>
</div>
</body>
</html>
"""


def normalize_phone(raw):
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw.strip().startswith("+"):
        return raw.strip()
    return None


def name_match(expected, cnam):
    if not cnam:
        return None
    expected_clean = expected.lower().strip()
    cnam_clean = cnam.lower().strip()
    ratio = difflib.SequenceMatcher(None, expected_clean, cnam_clean).ratio()
    expected_tokens = set(expected_clean.replace(",", " ").split())
    cnam_tokens = set(cnam_clean.replace(",", " ").split())
    token_overlap = len(expected_tokens & cnam_tokens) > 0
    return ratio > 0.5 or token_overlap


def lookup_number(e164_number):
    if not ACCOUNT_SID or not AUTH_TOKEN:
        raise RuntimeError("Server is missing Twilio credentials (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN).")

    params = {"Fields": "line_type_intelligence,caller_name"}
    resp = requests.get(
        LOOKUP_URL.format(number=e164_number),
        params=params,
        auth=(ACCOUNT_SID, AUTH_TOKEN),
        timeout=15,
    )
    if resp.status_code == 404:
        return {"valid": False, "line_type": None, "carrier": None, "cnam": None}
    resp.raise_for_status()
    data = resp.json()
    valid = data.get("valid", None)
    line_type = None
    carrier = None
    if data.get("line_type_intelligence"):
        line_type = data["line_type_intelligence"].get("type")
        carrier = data["line_type_intelligence"].get("carrier_name")
    cnam = None
    if data.get("caller_name"):
        cnam = data["caller_name"].get("caller_name")
    return {"valid": valid, "line_type": line_type, "carrier": carrier, "cnam": cnam}


def parse_leads(raw_text):
    """Parse pasted leads. Handles comma-separated ('Name, Phone') and
    tab-separated (pasted directly from Excel/Sheets, e.g. 'First\tLast\tPhone')
    formats. For multi-column rows, the last column is treated as the phone
    number and everything before it is joined as the name."""
    leads = []
    for line in raw_text.strip().splitlines():
        line = line.strip("\r\n")
        if not line.strip():
            continue

        # Prefer tab-splitting if tabs are present (Excel/Sheets paste)
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t") if p.strip() != ""]
        else:
            parts = [p.strip() for p in line.split(",") if p.strip() != ""]

        if len(parts) < 2:
            continue

        phone = parts[-1]
        name = " ".join(parts[:-1])
        leads.append((name, phone))
    return leads


@app.route("/", methods=["GET"])
def index():
    return render_template_string(PAGE_TEMPLATE, results=None)


@app.route("/verify", methods=["POST"])
def verify():
    raw_input = request.form.get("leads", "")
    leads = parse_leads(raw_input)

    if not leads:
        return render_template_string(
            PAGE_TEMPLATE,
            error="No valid leads found. Use one per line: Name, Phone",
            raw_input=raw_input,
            results=None,
        )

    results = []
    try:
        for name, raw_phone in leads:
            e164 = normalize_phone(raw_phone)
            if not e164:
                results.append({
                    "name": name, "phone": raw_phone, "e164_phone": "",
                    "valid": "UNPARSEABLE", "line_type": "", "carrier": "",
                    "cnam_name": "", "verdict": "SKIP - bad format",
                })
                continue

            info = lookup_number(e164)
            match = name_match(name, info["cnam"])

            if not info["valid"]:
                verdict = "SKIP - invalid/disconnected"
            elif match is False:
                verdict = "CHECK NAME - CNAM mismatch"
            else:
                verdict = "CALL"

            results.append({
                "name": name, "phone": raw_phone, "e164_phone": e164,
                "valid": info["valid"], "line_type": info["line_type"] or "",
                "carrier": info["carrier"] or "", "cnam_name": info["cnam"] or "",
                "verdict": verdict,
            })
            time.sleep(0.1)
    except RuntimeError as e:
        return render_template_string(PAGE_TEMPLATE, error=str(e), raw_input=raw_input, results=None)

    # store for CSV download
    session_id = os.urandom(8).hex()
    session["results_id"] = session_id
    RESULTS_STORE[session_id] = results

    call_count = sum(1 for r in results if r["verdict"] == "CALL")
    check_count = sum(1 for r in results if r["verdict"].startswith("CHECK"))
    skip_count = sum(1 for r in results if r["verdict"].startswith("SKIP"))

    return render_template_string(
        PAGE_TEMPLATE,
        results=results,
        call_count=call_count,
        check_count=check_count,
        skip_count=skip_count,
        raw_input=raw_input,
    )


@app.route("/download", methods=["GET"])
def download():
    session_id = session.get("results_id")
    results = RESULTS_STORE.get(session_id, [])
    if not results:
        return "No results to download yet. Run a verification first.", 400

    output = io.StringIO()
    fieldnames = ["name", "phone", "e164_phone", "valid", "line_type", "carrier", "cnam_name", "verdict"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    return send_file(
        mem,
        mimetype="text/csv",
        as_attachment=True,
        download_name="verified_leads.csv",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

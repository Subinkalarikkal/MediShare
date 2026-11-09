import json

import streamlit as st

from components.header import page_header
from components.session import actor, db
from components.tables import show_table, to_csv
from services import settings_service

a = actor()
page_header("Audit Logs", "Append-only record of administrative and workflow actions")
c1, c2 = st.columns(2)
action = c1.text_input("Action contains (e.g. VERIFIED, REVIEW_, DISPOSAL)")
entity = c2.selectbox("Entity type", ["", "user", "organization", "organization_authorization", "medicine",
                                      "medicine_registration", "inventory_batch", "collection_request", "collection_centre",
                                      "disposal_record", "transfer_record", "system_setting", "role", "uploaded_file",
                                      "risk_assessment", "ai_assistant", "notification"],
                      format_func=lambda v: v.replace("_", " ").title() if v else "All")
with db() as s:
    rows = settings_service.audit_logs(s, a, action or None, entity or None)
st.caption(f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'} (latest 1,000)")
idx = show_table(rows, ["at", "actor", "action", "entity", "entity_id", "details"], selectable=True, key="audit")
if idx is not None:
    r = rows[idx]
    c1, c2 = st.columns(2)
    c1.markdown("**Before**")
    c1.code(json.dumps(r["before"], indent=2, default=str) if r["before"] else "—", language="json")
    c2.markdown("**After**")
    c2.code(json.dumps(r["after"], indent=2, default=str) if r["after"] else "—", language="json")
if rows:
    st.download_button("Export audit log (CSV)", to_csv([{k: v for k, v in r.items() if k not in ("before", "after")}
                                                         for r in rows]), "audit_log.csv", "text/csv")

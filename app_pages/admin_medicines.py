import streamlit as st

from components.header import notice, page_header
from components.session import act, actor, db
from components.tables import show_table
from services import medicine_service
from utils.constants import DosageForm, RestrictedClass

a = actor()
page_header("Medicine Master", "Identification data only – no clinical recommendations")
pretty = lambda v: v.replace("_", " ").title()  # noqa: E731
notice("Products classified as controlled, narcotic, psychotropic, restricted or cold-chain are automatically "
       "excluded from all pilot workflows.")

with db() as s:
    term = st.text_input("Search")
    meds = medicine_service.search_medicines(s, term, limit=500)
    rows = [dict(id=m.id, name=m.name, generic_name=m.generic_name, active_ingredient=m.active_ingredient,
                 manufacturer=m.manufacturer, category=m.category, dosage_form=m.dosage_form,
                 packaging=m.packaging_type, restricted=m.restricted_classification, sample=m.is_sample) for m in meds]
    idx = show_table(rows, selectable=True, key="meds")

    def fields(p=None, key="m"):
        p = p or {}
        c1, c2, c3 = st.columns(3)
        out = dict(name=c1.text_input("Medicine name *", p.get("name", ""), key=f"{key}n"),
                   generic_name=c2.text_input("Generic name", p.get("generic_name") or "", key=f"{key}g"),
                   active_ingredient=c3.text_input("Active ingredient", p.get("active_ingredient") or "", key=f"{key}a"),
                   manufacturer=c1.text_input("Manufacturer", p.get("manufacturer") or "", key=f"{key}m"),
                   category=c2.text_input("Category", p.get("category") or "", key=f"{key}c"),
                   packaging_type=c3.text_input("Packaging type", p.get("packaging") or "", key=f"{key}p"))
        forms = DosageForm.values()
        out["dosage_form"] = c1.selectbox("Dosage form", forms, index=forms.index(p.get("dosage_form", "OTHER")),
                                          format_func=pretty, key=f"{key}f")
        classes = RestrictedClass.values()
        out["restricted_classification"] = c2.selectbox("Restricted classification", classes,
                                                         index=classes.index(p.get("restricted", "NONE")),
                                                         format_func=pretty, key=f"{key}r")
        return out

    if idx is not None:
        sel = rows[idx]
        st.subheader(f"Edit {sel['name']}")
        with st.form("edit_med"):
            data = fields(sel, "e")
            if st.form_submit_button("Save changes", type="primary"):
                act(s, medicine_service.update_medicine, sel["id"], success="Medicine updated", **data)
    st.subheader("Add a medicine")
    with st.form("add_med", clear_on_submit=True):
        data = fields(key="n")
        if st.form_submit_button("Add medicine", type="primary"):
            act(s, medicine_service.create_medicine, success="Medicine added", **data)

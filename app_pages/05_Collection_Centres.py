import html

import streamlit as st

from components.header import footer, page_header
from components.session import current_actor, db
from components.tables import show_table
from services import collection_service
from utils.constants import KERALA_DISTRICTS
from utils.location_utils import KERALA_CENTER, district_centroid

page_header("Find Collection Centres", "Only verified centres holding an in-date collection authorization are listed")

f1, f2, f3, f4 = st.columns([2, 1, 2, 1])
district = f1.selectbox("District", [""] + KERALA_DISTRICTS, format_func=lambda x: x or "All districts")
pin = f2.text_input("PIN code", max_chars=6)
name = f3.text_input("Centre name")
available = f4.checkbox("Accepting requests", value=True)
near = district_centroid(district) if district else None
radius = st.slider("Within (km of district centre)", 5, 150, 60) if near else None

with db() as s:
    centres = collection_service.search_centres(s, district=None if near else (district or None), pin_code=pin or None,
                                                name=name or None, available_only=available, near=near, max_km=radius)

st.caption(f"{len(centres)} authorized centre(s) found")
map_col, list_col = st.columns([3, 2])
with map_col:
    try:
        import folium
        from streamlit_folium import st_folium

        located = [c for c in centres if c["latitude"] is not None]
        center = near or ((located[0]["latitude"], located[0]["longitude"]) if located else KERALA_CENTER)
        m = folium.Map(location=center, zoom_start=9 if near else 7, tiles="OpenStreetMap")
        for c in centres:
            if c["latitude"] is None:
                continue
            e = {k: html.escape(str(v)) for k, v in c.items()}
            popup = (f"<b>{e['name']}</b><br>{e['address']}<br>{e['hours']}<br>☎ {e['phone']}<br>"
                     f"Accepts: {e['categories']}")
            folium.Marker([c["latitude"], c["longitude"]], popup=folium.Popup(popup, max_width=260), tooltip=e["name"],
                          icon=folium.Icon(color="blue" if c["available"] else "gray", icon="plus-sign")).add_to(m)
        st_folium(m, height=460, use_container_width=True, returned_objects=[])
    except ImportError:
        pts = [{"lat": c["latitude"], "lon": c["longitude"]} for c in centres if c["latitude"] is not None]
        if pts:
            st.map(pts)
with list_col:
    for c in centres:
        with st.container(border=True):
            st.markdown(f"**{c['name']}**" + ("  ·  _sample data_" if c["sample"] else ""))
            st.caption(f"{c['address']} · {c['district']} {c['pin_code']}")
            dist = f" · {c['distance_km']} km" if c["distance_km"] is not None else ""
            st.write(f"🕘 {c['hours'] or 'Hours not listed'} · ☎ {c['phone']}{dist}")
            st.caption(f"Accepts: {c['categories']} · {'Accepting requests' if c['available'] else 'Not accepting requests'}")
    if not centres:
        st.info("No authorized centres match these filters.")

a = current_actor()
if a and "INDIVIDUAL" in a.roles and a.status == "ACTIVE":
    if st.button("Request a collection", type="primary", icon=":material/local_shipping:"):
        st.switch_page("app_pages/06_Collection_Requests.py")
footer()

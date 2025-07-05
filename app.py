import streamlit as st
import io
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

import requests
import yaml
import json
import time
import base64
from julep import Julep
import pandas as pd
from geopy.geocoders import Nominatim
import folium
from folium.plugins import AntPath
import streamlit.components.v1 as components

# --- Configuration ---
JULEP_KEY = st.secrets["julep"]["api_key"]
WEATHER_KEY = st.secrets["weather"]["api_key"]
UNSPLASH_KEY = st.secrets["unsplash"]["api_key"]

# Initialize clients
client = Julep(api_key=JULEP_KEY)
geolocator = Nominatim(user_agent="streamlit_foodie")

# --- Agent & Task ---
@st.cache_resource
def get_agent_task():
    agent = client.agents.create(
        name="FoodieTourAgent",
        model="claude-3.5-sonnet",
        about="Agent for interactive foodie tours with maps"
    )
    task_yaml = """
name: Foodie Tour Planner
main:
  - prompt:
      - role: system
        content: |-
          You are a fun food-and-travel assistant. Output JSON:
          dining (Indoor/Outdoor), dishes (3 strings), restaurants (map dish->[3]),
          itinerary (string), bonus_stop (string), trivia (string).
      - role: user
        content: |-
          City: ${steps[0].input.city}
          Temp: ${steps[0].input.temp}°C
          Condition: ${steps[0].input.condition}
          Preferences: ${steps[0].input.prefs}
          Surprise Bonus: ${steps[0].input.surprise}
          Output valid JSON only.
"""
    task_def = yaml.safe_load(task_yaml)
    task = client.tasks.create(agent_id=agent.id, **task_def)
    return agent, task

# --- Weather Fetching ---
@st.cache_data(ttl=3600)
def get_weather(city):
    resp = requests.get(
        "http://api.weatherapi.com/v1/current.json",
        params={"key": WEATHER_KEY, "q": city}, timeout=5
    )
    resp.raise_for_status()
    cur = resp.json()["current"]
    return cur["temp_c"], cur["condition"]["text"]

# --- Geocoding ---
@st.cache_data(ttl=86400)
def geocode_place(name, city):
    try:
        loc = geolocator.geocode(f"{name}, {city}")
        return (loc.latitude, loc.longitude) if loc else (None, None)
    except:
        return (None, None)

# --- Image Fetching ---
@st.cache_data(ttl=86400)
def get_image(query):
    url = "https://api.unsplash.com/search/photos"
    headers = {"Authorization": f"Client-ID {UNSPLASH_KEY}"}
    params = {"query": query, "per_page": 1}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=3)
        resp.raise_for_status()
    except requests.exceptions.ReadTimeout:
        return None
    except Exception:
        return None
    data = resp.json().get("results", [])
    if data:
        return data[0]["urls"]["small"]
    return None

# --- Plan City (with progress) ---
def plan_city(city, prefs, surprise):
    progress = st.progress(0)
    step = 0

    # 1) Weather
    temp, cond = get_weather(city)
    progress.progress((step := step + 1) / 5)

    # 2) AI planner
    agent, task = get_agent_task()
    with st.spinner(f"Planning tour for {city}..."):
        ex = client.executions.create(
            task_id=task.id,
            input={"city": city, "temp": temp, "condition": cond,
                   "prefs": prefs, "surprise": surprise}
        )
        while True:
            res = client.executions.get(ex.id)
            if res.status in ("succeeded", "failed"): break
            time.sleep(0.5)
    if res.status != "succeeded":
        return None
    raw = res.output
    progress.progress((step := step + 1) / 5)

    # 3) Parse JSON output safely
    try:
        if isinstance(raw, list):
            assistant_msg = next((m for m in raw if m.get('role') == 'assistant'), None)
            if not assistant_msg:
                raise ValueError('No assistant message found')
            content = assistant_msg.get('content', '')
            data = json.loads(content)
        elif isinstance(raw, dict):
            if 'dining' in raw:
                data = raw
            elif 'choices' in raw:
                choice = raw['choices'][0]
                msg = choice.get('message', {})
                content = msg.get('content', '')
                data = json.loads(content)
            else:
                raise ValueError('Unexpected dict structure')
        elif isinstance(raw, str):
            data = json.loads(raw)
        else:
            raise ValueError(f'Unrecognized type: {type(raw)}')
    except Exception as e:
        st.error(f"Error parsing AI output for {city}: {e}\nOutput was: {raw}")
        return None

    # Merge weather info
    data.update({"temp": temp, "cond": cond})
    progress.progress((step := step + 1) / 5)

    # 4) Geocode stops
    stops = []
    all_places = []
    for lst in data.get("restaurants", {}).values():
        all_places.extend(lst)
    if data.get("bonus_stop"):
        all_places.append(data["bonus_stop"])
    for place in all_places:
        lat, lon = geocode_place(place, city)
        if lat and lon:
            stops.append({"name": place, "lat": lat, "lon": lon})
    data["stops"] = stops
    progress.progress(1.0)
    return data

# --- PDF Generation ---
def generate_pdf(city, data):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 40
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, f"Foodie Tour Itinerary: {city}")
    y -= 30
    c.setFont("Helvetica", 12)
    c.drawString(40, y, f"Weather: {data['cond']}, {data['temp']}°C")
    y -= 20
    c.drawString(40, y, f"Dining Recommendation: {data['dining']}")
    y -= 30
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "Iconic Dishes:")
    y -= 20
    c.setFont("Helvetica", 12)
    for dish in data.get("dishes", []):
        c.drawString(60, y, f"- {dish}")
        y -= 15
    y -= 10
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "Top Restaurants:")
    y -= 20
    c.setFont("Helvetica", 12)
    for dish, rests in data.get("restaurants", {}).items():
        c.drawString(60, y, f"{dish}:")
        y -= 15
        for r in rests:
            c.drawString(80, y, f"- {r}")
            y -= 15
        y -= 5
    y -= 10
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "Full Itinerary:")
    y -= 20
    text = c.beginText(60, y)
    text.textLines(data.get("itinerary", ""))
    c.drawText(text)
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()

# --- UI ---
def main():
    st.set_page_config(layout="wide", page_title="Folium Map Foodie Tour")
    st.title("🍽️ Foodie Tour Planner")
    festival = st.sidebar.checkbox("🎪 Festival Mode", False)
    prefs = st.sidebar.multiselect(
        "Dietary Preferences",
        ["Vegetarian", "Vegan", "Gluten-Free", "Keto", "Paleo", "Pescatarian", "Halal", "Kosher", "None"],
        default=["None"]
    )
    surprise = st.sidebar.checkbox("Add Surprise Stop", True)
    cities = st.text_input("Cities (comma-separated)", "Paris, Tokyo")

    if st.button("Generate Tours"):
        tours = {}
        for city in [c.strip() for c in cities.split(",") if c.strip()]:
            data = plan_city(city, prefs, surprise)
            if data:
                tours[city] = data
            else:
                st.error(f"❌ Could not plan {city}")
        st.session_state.tours = tours

    for city, data in st.session_state.get("tours", {}).items():
        if not data: continue
        with st.expander(f"🍴 {city}", True):
            st.subheader(f"{city} — {data['cond']}, {data['temp']}°C")
            st.write(f"**Dining:** {data['dining']} (Click markers below)")

            stops = data.get("stops", [])
            if stops:
                m = folium.Map(location=[stops[0]['lat'], stops[0]['lon']], zoom_start=13, control_scale=True)
                coords = [(s['lat'], s['lon']) for s in stops]
                AntPath(coords, color="red", weight=5, delay=1000).add_to(m)
                for stop in stops:
                    img = get_image(stop['name'])
                    html = f"<b>{stop['name']}</b>"
                    if img:
                        html += f"<br><img src='{img}' width='150'>"
                    folium.Marker(location=[stop['lat'], stop['lon']], popup=html, tooltip=stop['name']).add_to(m)
                components.html(m._repr_html_(), height=500, scrolling=True)

            st.subheader("🍽️ Iconic Dishes & Preview Images")
            cols = st.columns(3)
            for i, dish in enumerate(data.get("dishes", [])):
                with cols[i]:
                    img = get_image(dish)
                    if img:
                        st.image(img, caption=dish, use_column_width=True)
                    else:
                        st.write(dish)

            st.subheader("📍 Top Restaurants & Surprise Stop")
            for d, rests in data.get("restaurants", {}).items():
                st.markdown(f"**{d}:** {', '.join(rests)}")
            if data.get("bonus_stop"): st.info(f"🎁 Surprise: {data['bonus_stop']}")

            st.subheader("🔍 Trivia/Challenge")
            st.write(data.get("trivia",""))
            st.subheader("🚗 One-day Itinerary")
            st.write(data.get("itinerary",""))

            txt = json.dumps({city: data}, indent=2)
            b64 = base64.b64encode(txt.encode()).decode()
            href_json = f"data:application/json;base64,{b64}"
            html_links = [f'<a href="{href_json}" download="{city}_itinerary.json">📥 Download JSON</a>']
            if REPORTLAB_AVAILABLE:
                pdf_bytes = generate_pdf(city, data)
                b64_pdf = base64.b64encode(pdf_bytes).decode()
                href_pdf = f"data:application/pdf;base64,{b64_pdf}"
                html_links.append(f'<a href="{href_pdf}" download="{city}_itinerary.pdf">📄 Download PDF</a>')
            else:
                html_links.append("<i>Install reportlab to enable PDF download</i>")
            st.markdown(" | ".join(html_links), unsafe_allow_html=True)
            if festival:
                st.balloons()

if __name__ == '__main__':
    main()

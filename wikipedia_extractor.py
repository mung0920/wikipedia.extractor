import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests
import streamlit as st
from bs4 import BeautifulSoup

BASE_URL = "https://en.wikipedia.org/wiki/"
LICENSE = "Creative Commons Attribution-ShareAlike 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


class ExtractorError(Exception):
    """Base error for the application."""


class WikipediaExtractor:
    """Fetches and extracts selected sections from Wikipedia."""

    def __init__(self, timeout=15):
        self.timeout = timeout
        self.title = ""
        self.url = ""
        self.sections = []

    @staticmethod
    def clean(text):
        text = re.sub(r"\[\s*\d+\s*\]", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def fetch(self, topic):
        topic = topic.strip()
        if not topic:
            raise ExtractorError("The topic cannot be empty.")
        url = BASE_URL + quote(topic.replace(" ", "_"), safe="_()-',")
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "ISOM3400-Wikipedia-Extractor/1.0"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as error:
            raise ExtractorError(f"A network error occurred: {error}") from error

        soup = BeautifulSoup(response.text, "html.parser")
        if soup.find(id="noarticletext"):
            raise ExtractorError(f'No Wikipedia article was found for "{topic}".')

        heading = soup.find("h1", id="firstHeading") or soup.find("h1")
        content = soup.find("div", id="mw-content-text") or soup.find("main")
        if heading is None or content is None:
            raise ExtractorError("The page could not be parsed; its structure may have changed.")

        self.title = heading.get_text(" ", strip=True)
        self.url = response.url
        self.sections = []
        for h2 in content.find_all("h2"):
            headline = h2.find(class_=re.compile(r"\bmw-headline\b"))
            text = (headline or h2).get_text(" ", strip=True).replace("[edit]", "").strip()
            if text:
                self.sections.append({"title": text, "heading": h2})
        if not self.sections:
            raise ExtractorError("No top-level sections were found in this article.")
        return self.title

    def extract_section(self, index):
        section = self.sections[index]
        next_heading = (
            self.sections[index + 1]["heading"]
            if index + 1 < len(self.sections) else None
        )
        lines, seen = [], set()
        for el in section["heading"].next_elements:
            if el is next_heading:
                break
            if not el.name:
                continue
            if el.name in {"h3", "h4", "h5", "h6"}:
                text = self.clean(el.get_text(" ", strip=True)).replace("[edit]", "").strip()
                if text and text not in seen:
                    lines.append(f"\n{text}\n")
                    seen.add(text)
            elif el.name == "p" and el.find_parent("li") is None:
                text = self.clean(el.get_text(" ", strip=True))
                if text and text not in seen:
                    lines.append(text)
                    seen.add(text)
            elif el.name == "li" and el.find_parent("li") is None:
                text = self.clean(el.get_text(" ", strip=True))
                if text and text not in seen:
                    lines.append(f"- {text}")
                    seen.add(text)
            elif el.name == "table":
                for row in el.find_all("tr"):
                    cells = [self.clean(c.get_text(" ", strip=True))
                             for c in row.find_all(["th", "td"])]
                    cells = [c for c in cells if c]
                    if cells:
                        lines.append("| " + " | ".join(cells) + " |")
        return "\n\n".join(lines).strip() or "[No readable text was found in this section.]"


def build_output(extractor, numbers):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "=" * 70,
        "WIKIPEDIA SECTION EXTRACT",
        "=" * 70,
        f"Article title: {extractor.title}",
        f"Source URL: {extractor.url}",
        f"Extraction timestamp: {timestamp}",
        f"Licence: {LICENSE}",
        f"Licence URL: {LICENSE_URL}",
        "=" * 70,
        "",
    ]
    chosen = set(numbers)
    for index, section in enumerate(extractor.sections):
        if index + 1 not in chosen:
            continue
        lines += [section["title"], "-" * len(section["title"]),
                  extractor.extract_section(index), ""]
    return "\n".join(lines).strip() + "\n"


def safe_filename(title):
    safe = re.sub(r'[<>:"/\\|?*]', "", title)
    return re.sub(r"\s+", "_", safe).strip("._")[:120] or "wikipedia_extract"


st.set_page_config(page_title="Wikipedia Section Extractor")
st.title("Wikipedia Section Extractor")
st.write("Search Wikipedia, choose one or more sections, and download them as a text file.")

topic = st.text_input("Enter a Wikipedia topic:")

if st.button("Search"):
    extractor = WikipediaExtractor()
    try:
        title = extractor.fetch(topic)
        st.session_state.extractor = extractor
        st.success(f"Loaded: {title}")
        st.write(extractor.url)
    except ExtractorError as error:
        st.error(str(error))

if "extractor" in st.session_state:
    extractor = st.session_state.extractor
    labels = [s["title"] for s in extractor.sections]
    chosen = st.multiselect(
        "Select section(s) to extract:",
        options=list(range(1, len(labels) + 1)),
        format_func=lambda n: f"{n}. {labels[n - 1]}",
    )
    if st.button("Extract"):
        if not chosen:
            st.warning("Please choose at least one section.")
        else:
            content = build_output(extractor, chosen)
            st.text_area("Preview", content, height=300)
            st.download_button(
                "Download .txt",
                content,
                file_name=f"{safe_filename(extractor.title)}.txt",
                mime="text/plain",
            )
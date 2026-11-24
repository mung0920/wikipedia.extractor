import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://en.wikipedia.org/wiki/"
LICENSE = "Creative Commons Attribution-ShareAlike 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
OUTPUT_DIR = Path("output")


class ExtractorError(Exception):
    """Base error for the application."""


# ----------------------------- core logic -----------------------------
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
            if response.status_code == 404:
                raise ExtractorError(f'No Wikipedia article was found for "{topic}".')
            response.raise_for_status()
        except ExtractorError:
            raise
        except requests.exceptions.RequestException as error:
            raise ExtractorError(f"A network error occurred: {error}") from error

        try:
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
        except ExtractorError:
            raise
        except Exception as error:
            raise ExtractorError(f"An unexpected parsing error occurred: {error}") from error
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


def parse_selection(user_input, maximum):
    text = user_input.strip().lower()
    if not text:
        raise ValueError("Enter at least one section number.")
    if text == "all":
        return list(range(1, maximum + 1))
    numbers = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError("The selection contains an empty value.")
        if "-" in part:
            if part.count("-") != 1:
                raise ValueError(f'"{part}" is not a valid range.')
            start, end = (s.strip() for s in part.split("-"))
            if not (start.isdigit() and end.isdigit()):
                raise ValueError(f'"{part}" is not a valid numeric range.')
            start, end = int(start), int(end)
            if start > end:
                raise ValueError(f'Range "{part}" must go from smaller to larger.')
            if start < 1 or end > maximum:
                raise ValueError(f'Range "{part}" is outside 1 to {maximum}.')
            numbers.extend(range(start, end + 1))
        else:
            if not part.isdigit():
                raise ValueError(f'"{part}" is not a valid section number.')
            number = int(part)
            if number < 1 or number > maximum:
                raise ValueError(f"Section numbers must be between 1 and {maximum}.")
            numbers.append(number)
    return sorted(set(numbers))


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


def save_file(content, title):
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"{safe_filename(title)}.txt"
        counter = 1
        while path.exists():
            path = OUTPUT_DIR / f"{safe_filename(title)}_{counter}.txt"
            counter += 1
        path.write_text(content, encoding="utf-8")
        return path
    except OSError as error:
        raise ExtractorError(f"The output file could not be created: {error}") from error


# --------------------------- console interface ---------------------------
def console_request_selection(maximum):
    while True:
        print("\nEnter numbers, commas, ranges, or 'all'.")
        print("Examples: 1 | 1,3,5 | 2-4 | all")
        try:
            return parse_selection(input("Sections to extract: "), maximum)
        except ValueError as error:
            print(f"Invalid selection: {error}\nPlease try again.")


def console_process_article():
    extractor = WikipediaExtractor()
    while True:
        topic = input("\nTopic (or Q to go back): ").strip()
        if topic.lower() == "q":
            return
        try:
            title = extractor.fetch(topic)
            break
        except ExtractorError as error:
            print(f"Error: {error}")
            if input("Try another topic? (y/n): ").strip().lower() != "y":
                return
    print(f"\nLoaded: {title}")
    print(f"URL: {extractor.url}")
    print("\nAvailable sections:")
    for i, section in enumerate(extractor.sections, start=1):
        print(f"{i}. {section['title']}")
    try:
        numbers = console_request_selection(len(extractor.sections))
        path = save_file(build_output(extractor, numbers), title)
    except ExtractorError as error:
        print(f"Error: {error}")
        return
    print(f"\nDone. Sections {numbers} saved to: {path.resolve()}")


def run_console():
    print("=" * 60)
    print("WIKIPEDIA SECTION EXTRACTOR")
    print("=" * 60)
    print("\nThis program searches Wikipedia, lists an article's top-level")
    print("sections, lets you choose which to extract (by number, comma-")
    print("separated list, range, or 'all'), and saves the result as a")
    print("structured text file in the 'output' folder.")
    while True:
        choice = input("\n1. Extract an article\n2. Exit\nSelect an option: ").strip()
        if choice == "1":
            console_process_article()
        elif choice == "2":
            print("\nGoodbye!")
            break
        else:
            print("Invalid option. Enter 1 or 2.")


# --------------------------- streamlit interface -------------------------
def run_streamlit():
    import streamlit as st

    st.set_page_config(page_title="Wikipedia Section Extractor")
    st.title("Wikipedia Section Extractor")
    st.info(
        "Welcome! This app searches Wikipedia, lists an article's top-level "
        "sections, lets you choose one or more of them, and lets you download "
        "the result as a structured text file. To use it: type a topic below "
        "and click 'Search', then select your section(s) and click 'Extract'."
    )

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


# ------------------------------ entry point ------------------------------
def running_in_streamlit():
    """True only when launched with `streamlit run`."""
    try:
        import streamlit.runtime
        return streamlit.runtime.exists()
    except Exception:
        return False


def main():
    if running_in_streamlit():
        run_streamlit()
    else:
        run_console()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nStopped by the user.")

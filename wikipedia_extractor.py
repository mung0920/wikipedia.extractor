import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://en.wikipedia.org/wiki/"
LICENSE = "Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0)"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
OUTPUT_DIR = Path("output")
PREVIEW_CHARS = 1500


class ExtractorError(Exception):
    """Base error for the application."""


# ----------------------------- core logic -----------------------------
class WikipediaExtractor:
    """Fetches a Wikipedia article and extracts selected sections."""

    def __init__(self, timeout=15):
        self.timeout = timeout
        self.title = ""
        self.url = ""
        self.sections = []          # list of {"title": str, "heading": Tag}
        self.is_disambiguation = False

    # ---------- text helpers ----------
    @staticmethod
    def clean(text):
        """Remove citation markers such as [1], [23], [a], [note 1] and tidy spaces."""
        text = re.sub(r"\[\s*(?:\d+|[a-z]|note\s*\d+|citation needed|edit)\s*\]",
                      "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()

    # ---------- fetch / parse ----------
    def fetch(self, topic):
        """Download and parse the article, then build the section list."""
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
        except requests.exceptions.Timeout as error:
            raise ExtractorError("The request timed out. Please try again.") from error
        except requests.exceptions.ConnectionError as error:
            raise ExtractorError("Could not connect. Check your internet connection.") from error
        except requests.exceptions.HTTPError as error:
            raise ExtractorError(f"HTTP error: {error}") from error
        except requests.exceptions.RequestException as error:
            raise ExtractorError(f"A network error occurred: {error}") from error

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            if soup.find(id="noarticletext"):
                raise ExtractorError(f'No Wikipedia article was found for "{topic}".')

            heading = soup.find("h1", id="firstHeading") or soup.find("h1")
            content = soup.find("div", id="mw-content-text") or soup.find("main")
            if heading is None or content is None:
                raise ExtractorError(
                    "The page could not be parsed; its structure may have changed.")

            self.title = heading.get_text(" ", strip=True)
            self.url = response.url
            self.is_disambiguation = soup.find(id="disambigbox") is not None
            self.sections = []

            for h2 in content.find_all("h2"):
                headline = h2.find(class_=re.compile(r"\bmw-headline\b"))
                text = self.clean((headline or h2).get_text(" ", strip=True))
                if text and text.lower() != "contents":
                    self.sections.append({"title": text, "heading": h2})

            if not self.sections:
                raise ExtractorError("No top-level sections were found in this article.")
        except ExtractorError:
            raise
        except Exception as error:
            raise ExtractorError(
                f"An unexpected parsing error occurred: {error}") from error
        return self.title

    # ---------- extraction ----------
    def extract_section(self, index):
        """Return the readable text of one section (by 0-based index)."""
        section = self.sections[index]
        next_heading = (self.sections[index + 1]["heading"]
                        if index + 1 < len(self.sections) else None)

        lines = []
        seen_headings = set()

        for el in section["heading"].next_elements:
            if el is next_heading:
                break
            if not getattr(el, "name", None):
                continue

            # Subheadings
            if el.name in {"h3", "h4", "h5", "h6"}:
                text = self.clean(el.get_text(" ", strip=True))
                if text and text not in seen_headings:
                    lines.append(f"\n[{text}]")
                    seen_headings.add(text)

            # Paragraphs (skip those inside lists or tables)
            elif el.name == "p" and el.find_parent(["li", "table"]) is None:
                text = self.clean(el.get_text(" ", strip=True))
                if text:
                    lines.append(text)

            # List items (top-level items only, not inside tables)
            elif el.name == "li" and el.find_parent(["li", "table"]) is None:
                text = self.clean(el.get_text(" ", strip=True))
                if not text:
                    continue
                parent = el.find_parent(["ol", "ul"])
                if parent is not None and parent.name == "ol":
                    number = len(el.find_previous_siblings("li")) + 1
                    lines.append(f"{number}. {text}")
                else:
                    lines.append(f"- {text}")

            # Tables (skip tables nested inside other tables)
            elif el.name == "table" and el.find_parent("table") is None:
                for row in el.find_all("tr"):
                    cells = [self.clean(c.get_text(" ", strip=True))
                             for c in row.find_all(["th", "td"])]
                    cells = [c for c in cells if c]
                    if cells:
                        lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines).strip() or "[No readable text was found in this section.]"


# ----------------------------- helper functions -----------------------------
def parse_selection(user_input, maximum):
    """Turn input like '1,3,5', '2-4' or 'all' into a sorted list of ints."""
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
    """Assemble the full text (header + selected sections)."""
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
    """Make a filesystem-safe filename stem from an article title."""
    safe = re.sub(r'[<>:"/\\|?*]', "", title)
    return re.sub(r"\s+", "_", safe).strip("._")[:120] or "wikipedia_extract"


def save_file(content, title):
    """Write content to output/<title>.txt, choosing a free name if needed."""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(title)
        path = OUTPUT_DIR / f"{stem}.txt"
        counter = 1
        while path.exists():
            path = OUTPUT_DIR / f"{stem}_{counter}.txt"
            counter += 1
        path.write_text(content, encoding="utf-8")
        return path
    except OSError as error:
        raise ExtractorError(f"The output file could not be created: {error}") from error


# --------------------------- console interface ---------------------------
def ask_yes_no(prompt):
    """Keep asking until the user answers y or n."""
    while True:
        answer = input(f"{prompt} (y/n): ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer 'y' or 'n'.")


def request_selection(maximum):
    """Prompt until a valid section selection is entered."""
    while True:
        print("\nEnter section numbers, comma-separated lists, ranges, or 'all'.")
        print("Examples: 1  |  1,3,5  |  2-4  |  all")
        try:
            return parse_selection(input("Sections to extract: "), maximum)
        except ValueError as error:
            print(f"Invalid selection: {error}\nPlease try again.")


def load_article():
    """Ask for a topic until an article loads. Returns extractor or None."""
    extractor = WikipediaExtractor()
    while True:
        topic = input("\nEnter a topic (or Q to go back): ").strip()
        if topic.lower() == "q":
            return None
        try:
            extractor.fetch(topic)
            return extractor
        except ExtractorError as error:
            print(f"Error: {error}")
            print("Please try a different topic.")


def show_sections(extractor):
    print(f"\nLoaded: {extractor.title}")
    print(f"URL: {extractor.url}")
    if extractor.is_disambiguation:
        print("Note: this is a disambiguation page. Try a more specific topic "
              "if the sections below are not what you expected.")
    print("\nAvailable sections:")
    for i, section in enumerate(extractor.sections, start=1):
        print(f"  {i}. {section['title']}")


def process_article():
    """Full workflow for one article: search, list, select, preview, save."""
    extractor = load_article()
    if extractor is None:
        return

    show_sections(extractor)

    while True:
        numbers = request_selection(len(extractor.sections))
        try:
            content = build_output(extractor, numbers)
        except Exception as error:      # unexpected parsing failure
            print(f"Error: extraction failed ({error}). Please choose again.")
            continue

        if ask_yes_no("\nPreview the extracted text on screen?"):
            print("\n" + "-" * 60)
            print(content[:PREVIEW_CHARS])
            if len(content) > PREVIEW_CHARS:
                print(f"\n... (preview truncated, {len(content)} characters in total)")
            print("-" * 60)

        if ask_yes_no("Save to a .txt file?"):
            try:
                path = save_file(content, extractor.title)
                print(f"\nDone. Sections {numbers} saved to:\n{path.resolve()}")
            except ExtractorError as error:
                print(f"Error: {error}")
        else:
            print("Not saved.")

        if not ask_yes_no("\nExtract different sections from this article?"):
            return
        show_sections(extractor)


def run_console():
    print("=" * 60)
    print("WIKIPEDIA SECTION EXTRACTOR")
    print("=" * 60)
    print("\nThis program searches Wikipedia for a topic, lists the article's")
    print("top-level sections, and lets you choose which ones to extract")
    print("(by number, comma-separated list, range, or 'all'). The extracted")
    print("text is saved as a .txt file in the 'output' folder.")
    print("\nHow to use: choose option 1, type a topic, pick your sections,")
    print("then preview and save. Choose option 2 any time to exit.")

    while True:
        choice = input("\n1. Search and extract an article\n2. Exit\nSelect an option: ").strip()
        if choice == "1":
            process_article()
        elif choice == "2":
            print("\nGoodbye!")
            break
        else:
            print("Invalid option. Please enter 1 or 2.")


# --------------------------- streamlit interface -------------------------
def run_streamlit():
    import streamlit as st      # imported here so console mode never needs it

    st.set_page_config(page_title="Wikipedia Section Extractor")
    st.title("Wikipedia Section Extractor")
    st.info(
        "Welcome! This app searches Wikipedia, lists an article's top-level "
        "sections, lets you choose one or more of them, and lets you save or "
        "download the result as a structured text file. To use it: type a "
        "topic and click 'Search', then select your section(s) and click 'Extract'."
    )

    topic = st.text_input("Enter a Wikipedia topic:")
    if st.button("Search"):
        extractor = WikipediaExtractor()
        try:
            title = extractor.fetch(topic)
            st.session_state.extractor = extractor
            st.session_state.pop("content", None)
            st.success(f"Loaded: {title}")
            st.write(extractor.url)
            if extractor.is_disambiguation:
                st.warning("This is a disambiguation page. Try a more specific topic.")
        except ExtractorError as error:
            st.session_state.pop("extractor", None)
            st.session_state.pop("content", None)
            st.error(str(error))

    if "extractor" in st.session_state:
        extractor = st.session_state.extractor
        labels = [s["title"] for s in extractor.sections]

        selection_text = st.text_input(
            "Sections to extract (e.g. 1 | 1,3,5 | 2-4 | all):",
            help="Use the list below to see the section numbers.",
        )
        with st.expander("Available sections", expanded=True):
            for i, label in enumerate(labels, start=1):
                st.write(f"{i}. {label}")

        if st.button("Extract"):
            try:
                numbers = parse_selection(selection_text, len(labels))
                st.session_state.content = build_output(extractor, numbers)
                st.session_state.numbers = numbers
            except ValueError as error:
                st.session_state.pop("content", None)
                st.error(f"Invalid selection: {error}")
            except Exception as error:
                st.session_state.pop("content", None)
                st.error(f"Extraction failed: {error}")

        if "content" in st.session_state:
            content = st.session_state.content
            st.text_area("Preview", content, height=300)

            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "Download .txt",
                    content,
                    file_name=f"{safe_filename(extractor.title)}.txt",
                    mime="text/plain",
                )
            with col2:
                if st.button("Save to 'output' folder"):
                    try:
                        path = save_file(content, extractor.title)
                        st.success(f"Saved to: {path.resolve()}")
                    except ExtractorError as error:
                        st.error(str(error))


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
    except (KeyboardInterrupt, EOFError):
        print("\n\nProgram stopped by the user. Goodbye!")

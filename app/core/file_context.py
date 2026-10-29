"""Yüklenen dosyalardan Gemini'ye verilebilecek sınırlı metin bağlamı çıkarır."""

from __future__ import annotations

import posixpath
import threading
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree


MAX_CONTEXT_CHARS = 24_000
MAX_CACHE_ENTRIES = 24
MAX_EPUB_CHAPTERS = 80
MAX_EPUB_ENTRY_BYTES = 4_000_000
MAX_EPUB_TEXT = 120_000
_CACHE_LOCK = threading.Lock()
_CONTEXT_CACHE: dict[str, tuple[tuple[int, int], str]] = {}


class _EpubText(HTMLParser):
    SKIP = {"script", "style", "head", "title", "svg"}
    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCKS and not self._skip:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCKS and not self._skip:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            text = " ".join(data.split())
            if text:
                self.parts.append(text + " ")

    def result(self) -> str:
        raw = "".join(self.parts)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)



def extract_file_context(file_path: str) -> str:
    path = Path(file_path)
    try:
        info = path.stat()
    except OSError:
        return "Dosya bulunamadı."
    if not path.is_file():
        return "Dosya bulunamadı."

    key = str(path.resolve())
    signature = (info.st_mtime_ns, info.st_size)
    with _CACHE_LOCK:
        cached = _CONTEXT_CACHE.get(key)
        if cached and cached[0] == signature:
            return cached[1]

    result = _extract(path)
    if not result.startswith("Dosya okunamadı") and not result.startswith("'"):
        with _CACHE_LOCK:
            if len(_CONTEXT_CACHE) >= MAX_CACHE_ENTRIES:
                _CONTEXT_CACHE.pop(next(iter(_CONTEXT_CACHE)))
            _CONTEXT_CACHE[key] = (signature, result)
    return result


def _extract(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".py", ".json", ".csv", ".log", ".html", ".xml"}:
            text = path.read_text(encoding="utf-8", errors="replace")
        elif suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        elif suffix == ".docx":
            text = _read_docx(path)
        elif suffix == ".xlsx":
            text = _read_xlsx(path)
        elif suffix == ".pptx":
            text = _read_pptx(path)
        elif suffix == ".odt":
            text = _read_odt(path)
        elif suffix == ".epub":
            text = _read_epub(path)
        else:
            return f"'{path.name}' için metin çıkarma henüz desteklenmiyor. Desteklenen türler: metin, kod, CSV, JSON, PDF, DOCX, XLSX, PPTX, ODT ve EPUB."
    except Exception as error:
        return f"Dosya okunamadı: {error}"

    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n\n[Dosyanın kalan kısmı bağlam sınırı nedeniyle eklenmedi.]"
    return f"YÜKLENEN DOSYA: {path.name}\n---\n{text}"


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        raw = archive.read("word/document.xml")
    root = ElementTree.fromstring(raw)
    texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
    return "".join(texts)


def _read_xlsx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for node in root.iter():
                if node.tag.endswith("}si"):
                    shared.append("".join(child.text or "" for child in node.iter() if child.tag.endswith("}t")))
        names: list[str] = []
        if "xl/workbook.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            names = [
                node.attrib.get("name", f"Sayfa {index + 1}")
                for index, node in enumerate(root.iter())
                if node.tag.endswith("}sheet")
            ]
        sheets = sorted(
            (name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")),
            key=lambda value: int("".join(filter(str.isdigit, value.rsplit("/", 1)[-1])) or 0),
        )
        lines: list[str] = []
        for order, sheet in enumerate(sheets[:12]):
            title = names[order] if order < len(names) else f"Sayfa {order + 1}"
            lines.append(f"### {title}")
            root = ElementTree.fromstring(archive.read(sheet))
            row_count = 0
            for row in root.iter():
                if not row.tag.endswith("}row"):
                    continue
                cells: dict[int, str] = {}
                for cell in row:
                    if not cell.tag.endswith("}c"):
                        continue
                    ref = str(cell.attrib.get("r") or "")
                    column = 0
                    for char in ref:
                        if char.isalpha():
                            column = column * 26 + (ord(char.upper()) - 64)
                        else:
                            break
                    kind = cell.attrib.get("t")
                    value = ""
                    if kind == "inlineStr":
                        value = "".join(child.text or "" for child in cell.iter() if child.tag.endswith("}t"))
                    else:
                        raw = next((child.text for child in cell if child.tag.endswith("}v")), None)
                        if kind == "s" and raw is not None:
                            try:
                                value = shared[int(raw)]
                            except (ValueError, IndexError):
                                value = raw
                        elif kind == "b":
                            value = "EVET" if raw == "1" else "HAYIR"
                        else:
                            value = raw or ""
                    if value:
                        cells[max(column - 1, 0)] = " ".join(str(value).split())[:300]
                if not cells:
                    continue
                width = min(max(cells) + 1, 40)
                lines.append(" | ".join(cells.get(index, "") for index in range(width)))
                row_count += 1
                if row_count >= 200:
                    lines.append("… (satır sınırı)")
                    break
            lines.append("")
    return "\n".join(lines).strip()


def _read_pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        slides = sorted(
            (name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")),
            key=lambda value: int("".join(filter(str.isdigit, value.rsplit("/", 1)[-1])) or 0),
        )
        parts: list[str] = []
        for index, slide in enumerate(slides[:60], start=1):
            root = ElementTree.fromstring(archive.read(slide))
            texts = [
                " ".join((node.text or "").split())
                for node in root.iter()
                if node.tag.endswith("}t") and (node.text or "").strip()
            ]
            if texts:
                parts.append(f"### Slayt {index}\n" + "\n".join(texts))
    return "\n\n".join(parts)


def _read_odt(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("content.xml"))
    paragraphs = []
    for node in root.iter():
        if node.tag.endswith("}p") or node.tag.endswith("}h"):
            text = " ".join("".join(node.itertext()).split())
            if text:
                paragraphs.append(text)
    return "\n".join(paragraphs)


def _epub_has_encrypted_text(archive: zipfile.ZipFile, names: list[str]) -> bool:
    name = next((item for item in names if item.casefold() == "meta-inf/encryption.xml"), "")
    if not name:
        return False
    try:
        root = ElementTree.fromstring(archive.read(name))
    except (ElementTree.ParseError, KeyError, OSError):
        return True
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].casefold() != "cipherreference":
            continue
        uri = str(node.attrib.get("URI") or "").split("#", 1)[0].casefold()
        if uri.endswith((".xhtml", ".html", ".htm")):
            return True
    return False


def _epub_documents(archive: zipfile.ZipFile, names: list[str]) -> list[str]:
    opf_name = next((name for name in names if name.casefold().endswith(".opf")), "")
    documents: list[str] = []
    if opf_name:
        try:
            root = ElementTree.fromstring(archive.read(opf_name))
        except (ElementTree.ParseError, KeyError, OSError):
            root = None
        if root is not None:
            base = posixpath.dirname(opf_name)
            manifest: dict[str, str] = {}
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1].casefold() != "item":
                    continue
                item_id = str(node.attrib.get("id") or "")
                href = str(node.attrib.get("href") or "").split("#", 1)[0]
                media = str(node.attrib.get("media-type") or "").casefold()
                properties = {part.casefold() for part in str(node.attrib.get("properties") or "").split()}
                if not item_id or not href or "nav" in properties:
                    continue
                if "html" not in media and not href.casefold().endswith((".xhtml", ".html", ".htm")):
                    continue
                manifest[item_id] = posixpath.normpath(posixpath.join(base, href))
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1].casefold() != "itemref":
                    continue
                target = manifest.get(str(node.attrib.get("idref") or ""))
                if target and target in names and target not in documents:
                    documents.append(target)
    if not documents:
        documents = sorted(
            (
                name for name in names
                if name.casefold().endswith((".xhtml", ".html", ".htm"))
                and "/nav" not in name.casefold()
                and not name.casefold().startswith("meta-inf/")
            ),
            key=str.casefold,
        )
    return documents


def _read_epub(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if _epub_has_encrypted_text(archive, names):
            raise ValueError("DRM/şifreli EPUB desteklenmiyor.")
        documents = _epub_documents(archive, names)
        if not documents:
            raise ValueError("EPUB içinde okunabilir bölüm bulunamadı.")
        parts: list[str] = []
        total = 0
        for name in documents[:MAX_EPUB_CHAPTERS]:
            try:
                if archive.getinfo(name).file_size > MAX_EPUB_ENTRY_BYTES:
                    continue
                raw = archive.read(name)
            except (KeyError, OSError, zipfile.BadZipFile):
                continue
            parser = _EpubText()
            try:
                parser.feed(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            body = parser.result()
            if not body:
                continue
            remaining = MAX_EPUB_TEXT - total
            if remaining <= 0:
                break
            parts.append(body[:remaining])
            total += len(parts[-1])
    text = "\n\n".join(parts)
    if not text.strip():
        raise ValueError("EPUB içinde okunabilir metin bulunamadı.")
    return text

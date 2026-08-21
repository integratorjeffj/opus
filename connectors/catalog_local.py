"""Catalogue on disk: a folder of folders, one per piece.

This is the shape Opus has always read, now behind the CatalogSource contract.
Every remote source materialises into exactly this, so it is also the format
the rest of the system is written against.
"""

import csv
from pathlib import Path

from .base import BUILT, CatalogItem, CatalogSource, ConnectorError, register

CATALOG_FIELDS = ["item_title", "path", "notes"]
PDF_GLOB = "*.pdf"


def collect_pdfs(folder, recursive=True):
    """Every PDF under a folder, sorted so runs are reproducible."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    it = folder.rglob(PDF_GLOB) if recursive else folder.glob(PDF_GLOB)
    return sorted(p for p in it if p.is_file())


def scan_catalog(root):
    """Read a catalogue folder into CatalogItems. One subfolder is one piece.

    Refs are absolute. The generated map can be written anywhere -- often a
    cache folder in a different tree from the catalogue -- and load_catalog
    resolves relative entries against the map's own folder, so a relative ref
    would silently resolve to nothing.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise ConnectorError("Catalog folder not found: {}".format(root))
    items = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        pdfs = collect_pdfs(child)
        if pdfs:
            items.append(CatalogItem(title=child.name, ref=str(child),
                                     file_count=len(pdfs),
                                     notes="{} PDF(s)".format(len(pdfs))))
    # A flat folder of PDFs is a single piece, which is how a small publisher
    # with one product actually has things arranged.
    if not items:
        loose = collect_pdfs(root, recursive=False)
        if loose:
            items.append(CatalogItem(title=root.name, ref=str(root),
                                     file_count=len(loose),
                                     notes="{} PDF(s)".format(len(loose))))
    return items


def write_catalog_map(root, out_csv=None):
    """Write catalog_map.csv next to a catalogue folder. Returns its Path.

    Paths are written relative to the map file so a materialised catalogue can
    be moved or committed without breaking.
    """
    root = Path(root)
    out_csv = Path(out_csv) if out_csv else root / "catalog_map.csv"
    items = scan_catalog(root)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CATALOG_FIELDS, lineterminator="\n")
        w.writeheader()
        for item in items:
            try:
                rel = Path(item.ref).relative_to(out_csv.parent)
                path = str(rel).replace("\\", "/")
            except ValueError:
                path = item.ref
            w.writerow({"item_title": item.title, "path": path,
                        "notes": item.notes})
    return out_csv


@register
class LocalCatalog(CatalogSource):
    name = "local"
    label = "Local folder"
    description = "A folder on this machine, one subfolder per piece."
    state = BUILT

    def __init__(self, root=None):
        self.root = Path(root) if root else None

    def configure(self, root=None, **_ignored):
        if root:
            self.root = Path(root)
        return self

    def health(self):
        if not self.root:
            return False, "No catalog folder chosen."
        if not self.root.is_dir():
            return False, "Not a folder: {}".format(self.root)
        n = len(scan_catalog(self.root))
        if not n:
            return False, "No PDFs found under {}".format(self.root)
        return True, "{} piece(s) in {}".format(n, self.root)

    def list_items(self):
        if not self.root:
            raise ConnectorError("No catalog folder chosen.")
        return scan_catalog(self.root)

    def materialize(self, dest=None):
        """Already local. dest is accepted and ignored, by design."""
        if not self.root:
            raise ConnectorError("No catalog folder chosen.")
        if not self.root.is_dir():
            raise ConnectorError("Catalog folder not found: {}".format(self.root))
        return self.root

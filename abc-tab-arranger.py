#!/usr/bin/env python3
"""
ABC→Tab Arranger (CLI)

What it does
------------
- Keeps a **global chord library** outside the program (JSON file).
- Maintains a **songs/** folder. Each song has:
  - abc.txt              (the imported ABC notation)
  - mapping.json         (note→chord-name mapping used for this song)
  - tab.txt              (generated tablature output)
- Lets you **import** new ABC files from **abc_inbox/** (text files). After import,
  the source files are moved to **abc_processed/** to avoid duplicates.
- Menu options:
  1) Add chords to global library
  2) Convert ABC notation for new songs (import from inbox)
  3) Work on a song (edit mappings, generate tab)
  4) List chords / songs
  5) Rebuild tab for a song
  6) Quit

Notes & assumptions
-------------------
- Chord shapes are entered low→high as **E A D G B e** (six symbols): digits (fret number),
  or 'x' for muted. Example: E major open → "022100".
- ABC parsing here is intentionally simple: it extracts a flat sequence of note tokens and barlines ("|").
  It ignores ornaments, ties, tuplets, grace, etc. You can refine the parser as you need.
- Mapping is **note token → chord name** (e.g., "E2" → "E"). The tab generator then prints each chord
  across the line; barlines in ABC create separators in tab.
- Output tab is schematic (for editing). It prints chord symbols inline and a stacked six-string block
  for each chord. You can later customize layout to match your exact format.

Folder layout (auto-created on first run)
-----------------------------------------
project_root/
  data/
    chords.json
  abc_inbox/
  abc_processed/
  songs/

Run
---
python abc_tab_arranger.py

"""
from __future__ import annotations
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# =============== Paths & Setup ===============
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SONGS_DIR = ROOT / "songs"
INBOX_DIR = ROOT / "abc_inbox"
PROCESSED_DIR = ROOT / "abc_processed"
CHORDS_DB = DATA_DIR / "chords.json"

for p in [DATA_DIR, SONGS_DIR, INBOX_DIR, PROCESSED_DIR]:
    p.mkdir(parents=True, exist_ok=True)

# =============== Utilities ===============

def input_nonempty(prompt: str) -> str:
    while True:
        s = input(prompt).strip()
        if s:
            return s
        print("Please enter something.")

def yn(prompt: str) -> bool:
    while True:
        s = input(f"{prompt} [y/n]: ").strip().lower()
        if s in {"y", "yes"}: return True
        if s in {"n", "no"}: return False
        print("Type y or n.")

# =============== Chord Library ===============

@dataclass
class ChordShape:
    name: str      # e.g., "E", "Am", "Gadd9"
    shape: str     # six-char (or variable-width digits) low→high EADGBe; examples: "022100", "320003", "x02210"

    def validate(self) -> None:
        # Accept multi-digit frets; enforce 6 tokens composed of digits or 'x'
        tokens = re.findall(r"x|\d+", self.shape)
        # Tokenize from shape by scanning digits groups and x; also allow no separators
        # To ensure 6, rebuild by walking characters greedily.
        # Simpler: ensure that shape can be split into exactly 6 groups: we attempt heuristic split.
        groups = self._split_shape(self.shape)
        if len(groups) != 6:
            raise ValueError("Chord shape must describe exactly 6 strings (E A D G B e). Use digits or 'x'.")
        for g in groups:
            if not (g.isdigit() or g.lower() == 'x'):
                raise ValueError("Only digits and 'x' allowed in chord shape.")

    @staticmethod
    def _split_shape(shape: str) -> List[str]:
        # Allow compact like 022100 or with separators like 0-2-2-1-0-0
        s = shape.replace('-', '').replace(' ', '')
        # If length == 6 and all chars are digits/x, split per char (single-digit frets)
        if len(s) == 6 and re.fullmatch(r"[0-9xX]{6}", s):
            return list(s.lower())
        # Else, try slash/comma separated
        if any(sep in shape for sep in [',', '/', '|']):
            parts = re.split(r"[,/|]\s*", shape)
            return [p.strip().lower() for p in parts if p.strip()]
        # Else try space-separated groups
        if ' ' in shape:
            parts = [p.strip().lower() for p in shape.split(' ') if p.strip()]
            return parts
        # Fallback: attempt greedy group of digits/x into 6 chunks
        parts = re.findall(r"x|\d+", shape.lower())
        return parts

class ChordLibrary:
    def __init__(self, path: Path):
        self.path = path
        self.chords: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self.chords = json.loads(self.path.read_text(encoding='utf-8'))
            except Exception:
                print("Warning: chords.json is corrupted; starting fresh.")
                self.chords = {}
        else:
            self.chords = {}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.chords, indent=2, ensure_ascii=False), encoding='utf-8')

    def add_chord(self, name: str, shape: str) -> None:
        cs = ChordShape(name=name, shape=shape)
        cs.validate()
        self.chords[name] = shape
        self.save()

    def list_chords(self) -> List[Tuple[str, str]]:
        return sorted(self.chords.items())

    def find(self, query: str) -> List[Tuple[str, str]]:
        q = query.lower()
        return [(n,s) for n,s in self.chords.items() if q in n.lower() or q in s.lower()]

# =============== ABC Parsing ===============

ABC_NOTE_RE = re.compile(r"([A-Ga-g][,#b]?\d*|'*|,*)|([|])")
# This regex is lenient: captures notes (with optional accidental and length), apostrophes/commas for octave, and barlines.

@dataclass
class AbcScore:
    title: str
    tokens: List[str]  # sequence of notes and '|' barlines

class AbcParser:
    @staticmethod
    def parse(abc_text: str, title_hint: Optional[str]=None) -> AbcScore:
        # naive title: take first non-empty line starting with 'T:' or use hint
        title = title_hint or AbcParser._extract_title(abc_text) or "Untitled"
        tokens: List[str] = []
        for m in ABC_NOTE_RE.finditer(abc_text):
            tok = m.group(0)
            if tok.strip():
                tokens.append(tok)
        # Squash runs of barlines into single '|'
        squashed: List[str] = []
        for t in tokens:
            if t == '|' and squashed and squashed[-1] == '|':
                continue
            squashed.append(t)
        return AbcScore(title=title, tokens=squashed)

    @staticmethod
    def _extract_title(abc: str) -> Optional[str]:
        for line in abc.splitlines():
            if line.strip().startswith('T:'):
                return line.split(':',1)[1].strip()
        return None

# =============== Songs ===============

@dataclass
class Song:
    name: str
    path: Path
    abc_path: Path
    mapping_path: Path
    tab_path: Path

    @staticmethod
    def from_name(name: str) -> 'Song':
        safe = re.sub(r"[^A-Za-z0-9._\- ]+", "_", name).strip() or "Untitled"
        sp = SONGS_DIR / safe
        return Song(
            name=safe,
            path=sp,
            abc_path=sp / "abc.txt",
            mapping_path=sp / "mapping.json",
            tab_path=sp / "tab.txt",
        )

    def ensure_dirs(self):
        self.path.mkdir(parents=True, exist_ok=True)

    def load_mapping(self) -> Dict[str, str]:
        if self.mapping_path.exists():
            try:
                return json.loads(self.mapping_path.read_text(encoding='utf-8'))
            except Exception:
                print("Warning: mapping.json corrupted; starting empty.")
        return {}

    def save_mapping(self, mapping: Dict[str, str]):
        self.mapping_path.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding='utf-8')

# =============== Tab Generation ===============

STRING_ORDER = ["E", "A", "D", "G", "B", "e"]  # printed top→bottom as usual tab (we'll print E at bottom)

def split_shape_6(shape: str) -> List[str]:
    groups = ChordShape._split_shape(shape)
    if len(groups) != 6:
        raise ValueError("Chord shape must have 6 entries.")
    return groups

class TabGenerator:
    @staticmethod
    def chord_block(name: str, shape: str) -> str:
        groups = split_shape_6(shape)
        # Print as six lines e..E (top string first in tab is high e). We currently store E→e; flip.
        e_to_E = list(reversed(groups))
        lines = [f"{s}|- {fret}" for s, fret in zip(["e","B","G","D","A","E"], e_to_E)]
        title = f"[{name}]"
        return title + "\n" + "\n".join(lines)

    @staticmethod
    def generate_tab(tokens: List[str], mapping: Dict[str, str], lib: ChordLibrary) -> str:
        out_lines: List[str] = []
        segment: List[str] = []
        for tok in tokens:
            if tok == '|':
                # flush segment as a bar
                if segment:
                    out_lines.append(" | ".join(segment))
                    out_lines.append("-")
                    segment = []
                else:
                    out_lines.append("|")
                continue
            note = tok
            chord_name = mapping.get(note)
            if not chord_name:
                segment.append(f"{note} → [unmapped]")
                continue
            shape = lib.chords.get(chord_name)
            if not shape:
                segment.append(f"{note} → {chord_name} [missing in lib]")
                continue
            segment.append(f"{note} → {chord_name}")
            # Also append a mini stacked block for this chord
            segment.append(TabGenerator.chord_block(chord_name, shape))
        if segment:
            out_lines.append(" | ".join(segment))
        return "\n".join(out_lines)

# =============== Import ABC from inbox ===============

class Importer:
    @staticmethod
    def import_new_abc() -> List[Song]:
        imported: List[Song] = []
        for p in sorted(INBOX_DIR.glob("*.txt")):
            text = p.read_text(encoding='utf-8', errors='ignore')
            score = AbcParser.parse(text, title_hint=p.stem)
            song = Song.from_name(score.title)
            song.ensure_dirs()
            song.abc_path.write_text(text, encoding='utf-8')
            # Initialize empty mapping if not present
            if not song.mapping_path.exists():
                song.save_mapping({})
            # Move original inbox file to processed
            PROCESSED_DIR.mkdir(exist_ok=True)
            dest = PROCESSED_DIR / p.name
            shutil.move(str(p), str(dest))
            imported.append(song)
            print(f"Imported '{song.name}' from {dest.name}")
        if not imported:
            print("No new ABC files found in abc_inbox/ (expect .txt).")
        return imported

# =============== Interaction Helpers ===============

def choose_from_list(title: str, items: List[str]) -> Optional[str]:
    if not items:
        print("(none)")
        return None
    print(f"\n{title}")
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it}")
    print("  0. Cancel")
    while True:
        s = input("Choose #: ").strip()
        if s.isdigit():
            n = int(s)
            if n == 0:
                return None
            if 1 <= n <= len(items):
                return items[n-1]
        print("Invalid choice.")

# =============== Workflows ===============

def workflow_add_chords(lib: ChordLibrary):
    print("\nAdd chords to global library. Enter blank name to stop.")
    while True:
        name = input("Chord name (e.g., E, Am, Gadd9): ").strip()
        if not name:
            break
        shape = input_nonempty("Shape E A D G B e (digits/x). Examples: 022100, x02210, 3 2 0 0 0 3: ")
        try:
            lib.add_chord(name, shape)
            print(f"Added {name} → {lib.chords[name]}")
        except Exception as e:
            print(f"Error: {e}")


def workflow_import_abc():
    print("\nImporting new ABC files from abc_inbox/ ...")
    Importer.import_new_abc()


def list_songs() -> List[Song]:
    songs: List[Song] = []
    for d in sorted(SONGS_DIR.iterdir()):
        if d.is_dir():
            s = Song.from_name(d.name)
            if s.abc_path.exists():
                songs.append(s)
    return songs


def workflow_work_on_song(lib: ChordLibrary):
    songs = list_songs()
    if not songs:
        print("No songs yet. Use option 2 to import ABC files.")
        return
    chosen = choose_from_list("Songs:", [s.name for s in songs])
    if not chosen:
        return
    song = Song.from_name(chosen)
    abc = song.abc_path.read_text(encoding='utf-8') if song.abc_path.exists() else ''
    score = AbcParser.parse(abc, title_hint=song.name)
    mapping = song.load_mapping()

    while True:
        print(f"\nWorking on: {song.name}")
        print("  1) View current mapping summary")
        print("  2) Map notes → chords")
        print("  3) Generate tab")
        print("  4) Edit ABC title or rename song folder")
        print("  0) Back")
        choice = input("Choose: ").strip()
        if choice == '1':
            used = sorted({t for t in score.tokens if t != '|'})
            print("Notes in score:", ", ".join(used) if used else "(none)")
            print("Mapped:")
            for n, c in sorted(mapping.items()):
                print(f"  {n} → {c}")
            missing = [n for n in used if n not in mapping]
            print(f"Missing ({len(missing)}):", ", ".join(missing))
        elif choice == '2':
            # interactive mapping
            notes = sorted({t for t in score.tokens if t != '|'}, key=lambda x: x)
            if not notes:
                print("No note tokens found in ABC.")
                continue
            for note in notes:
                print(f"\nNote: {note}")
                current = mapping.get(note)
                if current:
                    print(f"  Current: {current}")
                    if not yn("  Change it?"):
                        continue
                # choose chord
                while True:
                    q = input("  Filter chords (name/shape, blank to list all): ").strip()
                    results = lib.find(q) if q else lib.list_chords()
                    if not results:
                        print("  No matches. You can add a new chord.")
                        if yn("  Add new chord?"):
                            cname = input_nonempty("    Chord name: ")
                            cshape = input_nonempty("    Shape E A D G B e: ")
                            try:
                                lib.add_chord(cname, cshape)
                            except Exception as e:
                                print(f"    Error: {e}")
                                continue
                            mapping[note] = cname
                            break
                        else:
                            continue
                    # display choices
                    for i, (nme, shp) in enumerate(results, 1):
                        print(f"   {i:>2}. {nme:<10} {shp}")
                    idx = input("  Pick #: ").strip()
                    if idx.isdigit() and 1 <= int(idx) <= len(results):
                        mapping[note] = results[int(idx)-1][0]
                        break
                    print("  Invalid choice.")
            song.save_mapping(mapping)
            print("Saved mapping.")
        elif choice == '3':
            tab = TabGenerator.generate_tab(score.tokens, mapping, lib)
            song.tab_path.write_text(tab, encoding='utf-8')
            print(f"Tab written to {song.tab_path}")
        elif choice == '4':
            newname = input_nonempty("New song name: ")
            new_song = Song.from_name(newname)
            if new_song.path.exists():
                print("A song with that name already exists.")
            else:
                new_song.ensure_dirs()
                # move files
                for src, dest in [
                    (song.abc_path, new_song.abc_path),
                    (song.mapping_path, new_song.mapping_path),
                    (song.tab_path, new_song.tab_path),
                ]:
                    if src.exists():
                        dest.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
                # remove old
                try:
                    shutil.rmtree(song.path)
                except Exception:
                    pass
                song = new_song
                print(f"Renamed to {song.name}")
        elif choice == '0':
            break
        else:
            print("Unknown option.")


def workflow_rebuild_tab(lib: ChordLibrary):
    songs = list_songs()
    if not songs:
        print("No songs found.")
        return
    chosen = choose_from_list("Rebuild tab for which song?", [s.name for s in songs])
    if not chosen:
        return
    song = Song.from_name(chosen)
    abc = song.abc_path.read_text(encoding='utf-8')
    score = AbcParser.parse(abc, title_hint=song.name)
    mapping = song.load_mapping()
    tab = TabGenerator.generate_tab(score.tokens, mapping, lib)
    song.tab_path.write_text(tab, encoding='utf-8')
    print(f"Rebuilt: {song.tab_path}")

# =============== Main Menu ===============

def main():
    lib = ChordLibrary(CHORDS_DB)
    while True:
        print("\nABC→Tab Arranger")
        print(" 1) Add chords to global library")
        print(" 2) Convert ABC notation for new songs (import from inbox)")
        print(" 3) Work on a song")
        print(" 4) List chords / songs")
        print(" 5) Rebuild tab for a song")
        print(" 6) Quit")
        choice = input("Choose: ").strip()
        if choice == '1':
            workflow_add_chords(lib)
        elif choice == '2':
            workflow_import_abc()
        elif choice == '3':
            workflow_work_on_song(lib)
        elif choice == '4':
            print("\nChords:")
            for n, s in lib.list_chords():
                print(f"  {n:<10} {s}")
            print("\nSongs:")
            for s in list_songs():
                print(f"  {s.name}")
        elif choice == '5':
            workflow_rebuild_tab(lib)
        elif choice == '6':
            print("Bye.")
            break
        else:
            print("Unknown choice.")

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
ABC→Tab Arranger (CLI)

New in this version
-------------------
- Robust ABC import: only imports the tune body starting at the first `K:` line and after (headers like X:, T:, L:, M:, Q: ignored for melody/chord mapping).
- Melody→TAB: convert ABC melody into single‑note guitar tab (standard tuning), with 3 bars per printed line.
- Chord mapping UX: while mapping notes→chords, you can add a brand‑new chord and it will be added to the global library if not present.
- Back everywhere: every submenu offers a `0) Back` option.

Folder layout
-------------
project_root/
  data/chords.json
  abc_inbox/            # drop .txt ABC files here
  abc_processed/        # processed ABC files moved here
  songs/
    <SongName>/
      abc.txt           # raw ABC (body preserved)
      mapping.json      # note→chord mapping for this song
      tab.txt           # generated chord-block tab
      melody_tab.txt    # generated single-note melody tab (3 bars/line)

"""
from __future__ import annotations
import json
import re
import shutil
from dataclasses import dataclass
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

def input_nonempty(prompt: str, allow_back: bool = True) -> Optional[str]:
    while True:
        s = input(prompt).strip()
        if allow_back and s == '0':
            return None
        if s:
            return s
        print("Please enter something (or 0 to go back).")

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
    shape: str     # six entries low→high EADGBe; examples: "022100", "320003", "x02210"

    def validate(self) -> None:
        groups = self._split_shape(self.shape)
        if len(groups) != 6:
            raise ValueError("Chord shape must describe exactly 6 strings (E A D G B e). Use digits or 'x'.")
        for g in groups:
            if not (g.isdigit() or g.lower() == 'x'):
                raise ValueError("Only digits and 'x' allowed in chord shape.")

    @staticmethod
    def _split_shape(shape: str) -> List[str]:
        s = shape.replace('-', '').replace(' ', '')
        if len(s) == 6 and re.fullmatch(r"[0-9xX]{6}", s):
            return list(s.lower())
        if any(sep in shape for sep in [',', '/', '|']):
            parts = re.split(r"[,/|]\s*", shape)
            return [p.strip().lower() for p in parts if p.strip()]
        if ' ' in shape:
            return [p.strip().lower() for p in shape.split(' ') if p.strip()]
        return re.findall(r"x|\d+", shape.lower())

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

# =============== ABC Parsing (headers & body) ===============

HEADER_FIELD_RE = re.compile(r"^(X|T|S|Q|L|M|K):")
ABC_TOKEN_RE = re.compile(r"\^|_|=|[A-Ga-g]|[,']|\d+|\|+|\s+|\(|\)|:")

@dataclass
class AbcScore:
    title: str
    key: str
    tokens: List[str]  # sequence of tokens starting from body (after K:). Includes notes and '|'.

class AbcParser:
    @staticmethod
    def split_header_body(text: str) -> Tuple[List[str], List[str]]:
        lines = text.splitlines()
        header: List[str] = []
        body: List[str] = []
        have_key = False
        for ln in lines:
            if not have_key and HEADER_FIELD_RE.match(ln.strip()):
                header.append(ln)
                if ln.strip().startswith('K:'):
                    have_key = True
                continue
            if have_key:
                body.append(ln)
            else:
                if ln.strip().startswith('K:'):
                    header.append(ln)
                    have_key = True
                else:
                    header.append(ln)
        if not have_key:
            return header, lines
        return header, body

    @staticmethod
    def extract_title(header_lines: List[str], fallback: str) -> str:
        for ln in header_lines:
            if ln.strip().startswith('T:'):
                return ln.split(':',1)[1].strip() or fallback
        return fallback

    @staticmethod
    def extract_key(header_lines: List[str]) -> str:
        for ln in header_lines:
            if ln.strip().startswith('K:'):
                return ln.split(':',1)[1].strip()
        return 'C'

    @staticmethod
    def parse(text: str, title_hint: Optional[str]=None) -> AbcScore:
        header, body_lines = AbcParser.split_header_body(text)
        title = AbcParser.extract_title(header, title_hint or "Untitled")
        key = AbcParser.extract_key(header)
        body = "\n".join(body_lines)
        raw_tokens = [t for t in ABC_TOKEN_RE.findall(body) if not t.isspace()]
        tokens: List[str] = []
        for t in raw_tokens:
            if t.startswith('|'):
                if not tokens or tokens[-1] != '|':
                    tokens.append('|')
                continue
            tokens.append(t)
        return AbcScore(title=title, key=key, tokens=tokens)

# =============== Songs ===============

@dataclass
class Song:
    name: str
    path: Path
    abc_path: Path
    mapping_path: Path
    tab_path: Path
    melody_tab_path: Path

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
            melody_tab_path=sp / "melody_tab.txt",
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

# =============== Tab Generation (Chord blocks) ===============

STRING_ORDER = ["E", "A", "D", "G", "B", "e"]

def split_shape_6(shape: str) -> List[str]:
    groups = ChordShape._split_shape(shape)
    if len(groups) != 6:
        raise ValueError("Chord shape must have 6 entries.")
    return groups

class TabGenerator:
    @staticmethod
    def chord_block(name: str, shape: str) -> str:
        groups = split_shape_6(shape)
        e_to_E = list(reversed(groups))
        lines = [f"{s}|- {fret}" for s, fret in zip(["e","B","G","D","A","E"], e_to_E)]
        title = f"[{name}]"
        return title + "\n" + "\n".join(lines)

    @staticmethod
    def generate_tab(tokens: List[str], mapping: Dict[str, str], lib: ChordLibrary) -> str:
        out_lines: List[str] = []
        segment: List[str] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t == '|':
                if segment:
                    out_lines.append(" | ".join(segment))
                    out_lines.append("-")
                    segment = []
                else:
                    out_lines.append("|")
                i += 1
                continue
            # Build note token with following digits for duration
            note_token = t
            j = i + 1
            while j < len(tokens) and re.fullmatch(r"\d+", tokens[j]):
                note_token += tokens[j]
                j += 1
            chord_name = mapping.get(note_token)
            if not chord_name:
                segment.append(f"{note_token} → [unmapped]")
            else:
                shape = lib.chords.get(chord_name)
                if not shape:
                    segment.append(f"{note_token} → {chord_name} [missing in lib]")
                else:
                    segment.append(f"{note_token} → {chord_name}")
                    segment.append(TabGenerator.chord_block(chord_name, shape))
            i = j
        if segment:
            out_lines.append(" | ".join(segment))
        return "\n".join(out_lines)

# =============== Melody Tab (single‑note) ===============

OPEN_STRING_MIDI = {
    'E': 40,  # E2
    'A': 45,  # A2
    'D': 50,  # D3
    'G': 55,  # G3
    'B': 59,  # B3
    'e': 64,  # E4
}
STRINGS_HIGH_TO_LOW = ['e','B','G','D','A','E']

ABC_BASE_MIDI = {
    'C': 60, 'D': 62, 'E': 64, 'F': 65, 'G': 67, 'A': 69, 'B': 71,
    'c': 72, 'd': 74, 'e': 76, 'f': 77, 'g': 79, 'a': 81, 'b': 83,
}
ACCIDENTAL_OFFSET = {'^': 1, '_': -1, '=': 0}

@dataclass
class FretPos:
    string: str
    fret: int

class MelodyTabber:
    @staticmethod
    def abc_note_to_midi(token: str) -> Optional[int]:
        m = re.match(r"(?P<acc>\^|_|=)?(?P<note>[A-Ga-g])(?P<oct>[',]*)", token)
        if not m:
            return None
        acc = m.group('acc') or ''
        note = m.group('note')
        octmod = m.group('oct') or ''
        base = ABC_BASE_MIDI.get(note)
        if base is None:
            return None
        for ch in octmod:
            if ch == "'":
                base += 12
            elif ch == ',':
                base -= 12
        base += ACCIDENTAL_OFFSET.get(acc, 0)
        return base

    @staticmethod
    def choose_fret(midi: int) -> Optional[FretPos]:
        best: Optional[FretPos] = None
        for s in STRINGS_HIGH_TO_LOW:
            open_m = OPEN_STRING_MIDI[s]
            fret = midi - open_m
            if 0 <= fret <= 24:
                if best is None or fret < best.fret or (fret == best.fret and STRINGS_HIGH_TO_LOW.index(s) < STRINGS_HIGH_TO_LOW.index(best.string)):
                    best = FretPos(string=s, fret=fret)
        return best

    @staticmethod
    def tokens_to_notes(tokens: List[str]) -> List[str]:
        out: List[str] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t == '|':
                out.append('|')
                i += 1
                continue
            if re.fullmatch(r"\^|_|=|[A-Ga-g]", t):
                tok = t
                j = i + 1
                while j < len(tokens) and re.fullmatch(r"[,']", tokens[j]):
                    tok += tokens[j]
                    j += 1
                out.append(tok)
                i = j
                while i < len(tokens) and re.fullmatch(r"\d+", tokens[i]):
                    i += 1
            else:
                i += 1
        return out

    @staticmethod
    def build_bar_blocks(note_tokens: List[str]) -> List[List[str]]:
        bars: List[List[str]] = [[]]
        for t in note_tokens:
            if t == '|':
                bars.append([])
            else:
                bars[-1].append(t)
        if bars and not bars[-1]:
            bars.pop()
        return bars

    @staticmethod
    def render_3bars_per_line(bars: List[List[str]]) -> str:
        lines: List[str] = []
        for i in range(0, len(bars), 3):
            chunk = bars[i:i+3]
            string_lines = {s: [] for s in STRINGS_HIGH_TO_LOW}
            for bar in chunk:
                for note in bar:
                    midi = MelodyTabber.abc_note_to_midi(note)
                    pos = MelodyTabber.choose_fret(midi) if midi is not None else None
                    for s in STRINGS_HIGH_TO_LOW:
                        if pos and s == pos.string:
                            string_lines[s].append(str(pos.fret))
                        else:
                            string_lines[s].append('-')
                for s in STRINGS_HIGH_TO_LOW:
                    string_lines[s].append('|')
            for s in STRINGS_HIGH_TO_LOW:
                row = f"{s}| " + ' '.join(string_lines[s]).rstrip('|').rstrip()
                lines.append(row)
            lines.append("")
        return "\n".join(lines).rstrip()

    @staticmethod
    def generate_melody_tab(score: AbcScore) -> str:
        note_tokens = MelodyTabber.tokens_to_notes(score.tokens)
        bars = MelodyTabber.build_bar_blocks(note_tokens)
        return MelodyTabber.render_3bars_per_line(bars)

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
            if not song.mapping_path.exists():
                song.save_mapping({})
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
    print("  0. Back")
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
    print("\nAdd chords to global library. Enter blank name to stop. (0 to back)")
    while True:
        name = input("Chord name (e.g., E, Am, Gadd9) [0=back]: ").strip()
        if name == '0' or not name:
            break
        shape = input_nonempty("Shape E A D G B e (digits/x). Examples: 022100, x02210, 3 2 0 0 0 3 [0=back]: ")
        if shape is None:
            break
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


def ensure_chord_present_or_add(lib: ChordLibrary, cname: str) -> None:
    if cname not in lib.chords:
        print(f"Chord '{cname}' not in global library.")
        if yn("Add it now?"):
            while True:
                cshape = input_nonempty("  Shape E A D G B e: ")
                if cshape is None:
                    return
                try:
                    lib.add_chord(cname, cshape)
                    print(f"  Added {cname} → {lib.chords[cname]}")
                    return
                except Exception as e:
                    print(f"  Error: {e}")


def workflow_work_on_song(lib: ChordLibrary):
    songs = list_songs()
    if not songs:
        print("No songs yet. Use option 2 to import ABC files.")
        return
    chosen = choose_from_list("Songs:", [s.name for s in songs])
    if not chosen:
        return
    song = Song.from_name(chosen)
    abc_full = song.abc_path.read_text(encoding='utf-8') if song.abc_path.exists() else ''
    score = AbcParser.parse(abc_full, title_hint=song.name)
    mapping = song.load_mapping()

    while True:
        print(f"\nWorking on: {song.name}")
        print("  1) View mapping summary")
        print("  2) Map notes → chords (add/search, with back)")
        print("  3) Generate chord-block tab → tab.txt")
        print("  4) Generate MELODY tab (3 bars/line) → melody_tab.txt")
        print("  5) Edit ABC title or rename song folder")
        print("  0) Back")
        choice = input("Choose: ").strip()
        if choice == '1':
            used = sorted({t for t in MelodyTabber.tokens_to_notes(score.tokens) if t != '|'})
            print("Notes in score:", ", ".join(used) if used else "(none)")
            print("Mapped:")
            for n, c in sorted(mapping.items()):
                print(f"  {n} → {c}")
            missing = [n for n in used if n not in mapping]
            print(f"Missing ({len(missing)}):", ", ".join(missing))
        elif choice == '2':
            notes = sorted({t for t in MelodyTabber.tokens_to_notes(score.tokens) if t != '|'}, key=lambda x: x)
            if not notes:
                print("No note tokens found in ABC body.")
                continue
            for note in notes:
                print(f"\nNote: {note}")
                current = mapping.get(note)
                if current:
                    print(f"  Current: {current}")
                    if not yn("  Change it?"):
                        continue
                while True:
                    q = input("  Filter chords (name/shape, blank=list all, 'new' to add): ").strip()
                    if q.lower() == 'new':
                        cname = input_nonempty("    New chord name [0=back]: ")
                        if cname is None:
                            break
                        cshape = input_nonempty("    Shape E A D G B e [0=back]: ")
                        if cshape is None:
                            break
                        try:
                            lib.add_chord(cname, cshape)
                            mapping[note] = cname
                            print(f"    Added & mapped {note} → {cname}")
                            break
                        except Exception as e:
                            print(f"    Error: {e}")
                            continue
                    else:
                        results = lib.find(q) if q else lib.list_chords()
                        if not results:
                            print("  No matches. Type 'new' to add a chord.")
                            continue
                        for i, (nme, shp) in enumerate(results, 1):
                            print(f"   {i:>2}. {nme:<10} {shp}")
                        idx = input("  Pick # (0=back): ").strip()
                        if idx == '0':
                            break
                        if idx.isdigit() and 1 <= int(idx) <= len(results):
                            cname = results[int(idx)-1][0]
                            mapping[note] = cname
                            ensure_chord_present_or_add(lib, cname)
                            break
                        print("  Invalid choice.")
            song.save_mapping(mapping)
            print("Saved mapping.")
        elif choice == '3':
            tab = TabGenerator.generate_tab(score.tokens, mapping, lib)
            song.tab_path.write_text(tab, encoding='utf-8')
            print(f"Tab written to {song.tab_path}")
        elif choice == '4':
            melody = MelodyTabber.generate_melody_tab(score)
            song.melody_tab_path.write_text(melody, encoding='utf-8')
            print(f"Melody tab written to {song.melody_tab_path}")
        elif choice == '5':
            newname = input_nonempty("New song name [0=back]: ")
            if newname is None:
                continue
            new_song = Song.from_name(newname)
            if new_song.path.exists():
                print("A song with that name already exists.")
            else:
                new_song.ensure_dirs()
                for src, dest in [
                    (song.abc_path, new_song.abc_path),
                    (song.mapping_path, new_song.mapping_path),
                    (song.tab_path, new_song.tab_path),
                    (song.melody_tab_path, new_song.melody_tab_path),
                ]:
                    if src.exists():
                        dest.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
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
    abc_full = song.abc_path.read_text(encoding='utf-8')
    score = AbcParser.parse(abc_full, title_hint=song.name)
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

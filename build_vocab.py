#!/usr/bin/env python3
"""Builds vocab.txt, the word list JevTalk spells from, out of wordfreq's English frequency list.

    pip install wordfreq==3.1.1 && python3 build_vocab.py   # jevtalk.py also runs this itself on first use

vocab.txt isn't shipped with JevTalk: wordfreq's data is CC BY-SA 4.0, and its author asks people not to
redistribute it as a flat list (https://github.com/rspeer/wordfreq#license), so you build your own copy.
The rules below reproduce the list JevTalk was tuned on exactly, given wordfreq 3.1.1 and macOS's
/usr/share/dict/words. Other versions or
dictionaries give a slightly different list, which works but may shift eval scores a little.
"""
import hashlib
import re
from pathlib import Path

from wordfreq import top_n_list

LAST = "crass"           # the list stops here, about 30k words in
EXPECTED_SHA256 = "19ef0633614b47f3bbf12c1c4adf019789abfda7579639c5d4e7fc71aee02e5e"  # the list JevTalk was tuned on
ABBREVIATIONS = {"al", "co", "ii", "oh", "ok", "re", "st", "tv", "uk"}
INTERJECTIONS = {"yeah", "whoa", "huh"}
JUNK = {"n't", "a's", "t's", "y'know", "i's", "b's", "m's", "g's", "d's"}
# name particles, so Jev can spell "da Vinci"; each goes back where it sat in the tuned list
PARTICLES = {"de": "energy", "la": "professional", "da": "significantly", "di": "uniform", "du": "convicted",
             "von": "woman's"}


def build(dictionary):
    words = [w for w in top_n_list("en", 60000) if re.fullmatch(r"[a-z']+", w)]
    words = words[: words.index(LAST) + 1]
    keep = []
    for rank, w in enumerate(words):
        if len(w) == 1 and w not in ("a", "i"):
            continue
        if len(w) == 2 and (w in ABBREVIATIONS or (rank >= 1000 and w != "hi")):
            continue
        if len(w) == 3 and rank >= 2000 and w not in dictionary:  # res, des, mic, dna, ceo…
            continue
        if w in INTERJECTIONS or w in JUNK or w in PARTICLES:
            continue
        keep.append(w)
    for w, after in PARTICLES.items():
        keep.insert(keep.index(after) + 1, w)
    return keep


def main():
    dict_path = Path("/usr/share/dict/words")
    dictionary = {w.strip().lower() for w in dict_path.open()} if dict_path.exists() else set()
    words = build(dictionary)
    text = "\n".join(words) + "\n"
    Path(__file__).with_name("vocab.txt").write_text(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    same = digest == EXPECTED_SHA256
    print(f"wrote vocab.txt: {len(words):,} words"
          + ("" if same else " (differs from the reference list; fine, but eval scores may shift slightly)"))


if __name__ == "__main__":
    main()

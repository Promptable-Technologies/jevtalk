# JevTalk

JevTalk gets [Jev](https://openrouter.ai/typesafe/jev-1.13), TypeSafe AI's "System One" model, to answer questions
in plain English.

Jev can't write. You give it some text and a set of questions, and it returns probabilities: yes or no, a pick
from a list of options, or a score. JevTalk turns that into answers by making Jev **spell** them, one choice at a
time. Each step offers the answer so far followed by candidate next words (or the next few letters of a rarer
word), plus "I'm done", and Jev picks one.

```
$ python3 jevtalk.py "What color is grass?"
jev> Green.
[64 Jev calls, 180,890 input tokens, $0.0076, 7.9s]
```

## How it works

For each question, Jev:

1. picks how long the answer should be (1–3 words, 4–8, 9–15 or 16–30);
2. spells out the one key word the answer most needs ("honey");
3. writes 8 drafts in parallel, one word at a time. Every step also asks "is the answer finished?" and "is there a
   grammar mistake so far?"; when Jev flags a mistake, the last word is taken back and banned at that spot;
4. picks the best of the finished drafts, since judging whole texts is what it's best at;
5. polishes the winner: it compares the answer with versions that drop one word, add a common word, or swap an
   article ("They make a honey." → "They make honey.").

Option order is shuffled across a few copies of every question, because Jev slightly prefers options by
position. A typical answer takes 30–150 Jev calls, costs about a cent, and takes a few seconds.

**What it's good at:** short factual answers ("Da Vinci.", "A spider has 8 legs.", "Roots get water from the
soil."). On its 16-question benchmark it gets about 0.89 correct and 0.95 fluent, and about 0.79 / 0.89 on 16
questions never used for tuning. Note that Jev grades these itself.

**What it struggles with:** why/how questions and opinions ("Why do leaves change color?", "How do I make tea?").
Spelling one word at a time without a plan can't build a multi-step explanation.

## Setup

You need Python 3.9 or later and an [OpenRouter](https://openrouter.ai) API key with credit.

```sh
pip install wordfreq==3.1.1
export OPENROUTER_API_KEY=sk-or-...
python3 jevtalk.py "What do bees make?"
```

On macOS you can keep the key in your keychain instead of an environment variable. This prompts for it:

```sh
security add-generic-password -s OPENROUTER_API_KEY -a "$USER" -w
```

JevTalk checks `OPENROUTER_API_KEY` first, then the keychain item with that service name. To use a different item
name, set `JEVTALK_KEYCHAIN_SERVICE`.

### The word list

JevTalk spells from a list of about 29,000 common English words, `vocab.txt`. The repo doesn't include it.
It comes from [wordfreq](https://github.com/rspeer/wordfreq), whose data is licensed CC BY-SA 4.0, and wordfreq's
author asks people not to redistribute it as a flat word list. Instead, the first time you run JevTalk it builds
`vocab.txt` on your machine from your own copy of wordfreq; that's why the setup above installs it. You can also
build it yourself with `python3 build_vocab.py`.

With wordfreq 3.1.1 on macOS, the result matches the list JevTalk was tuned on exactly. Elsewhere it can differ
slightly, because one filter checks short words against `/usr/share/dict/words`. JevTalk still works, but
answers may differ a little.

## Command line

```sh
python3 jevtalk.py "Who painted the Mona Lisa?"   # one question
python3 jevtalk.py                                 # chat; follow-up questions see the conversation
python3 jevtalk.py -v "What do bees make?"         # show every decision Jev makes
python3 jevtalk.py --eval                          # 16-question benchmark, graded by Jev
python3 jevtalk.py --heldout                       # 16 questions never used for tuning
python3 jevtalk.py --selftest                      # offline check with a fake Jev, no API calls
python3 jevtalk.py --help                          # every tuning option
```

## As a library

```python
from jevtalk import reply

answer = reply("What do bees make?",
               on_text=print,                                  # the answer as it changes
               on_draft=lambda i, text, score=None: None)      # each of the 8 drafts as it's written
```

`reply` also takes `history=[(question, answer), ...]` for follow-up questions, and `context={...}` to give Jev
extra material to answer from.

## Mac app

A small app that shows Jev writing all 8 drafts at once, which one it picks, and how the answer gets polished.
Hover over an earlier answer and click × to remove it from the conversation, which is useful when one wrong
answer keeps steering the next ones.

```sh
./build_app.sh --install     # needs Xcode or the Command Line Tools (xcode-select --install)
open ~/Applications/JevTalk.app
```

The build script builds `vocab.txt` first if needed and packs it into the app. The app runs `jevtalk_app.py`,
a small local server, using the `python3` from your login shell, so it works with pyenv, Homebrew and so on. The
server only listens on 127.0.0.1 and requires a
token generated at each launch, so other web pages can't use it to spend your credit. Errors go to
`~/Library/Logs/JevTalk.log`.

To use the same interface in your browser instead (any OS): `python3 jevtalk_app.py --open`.

## When Jev is busy

Jev sometimes answers `529 system_overloaded` when it has a lot of traffic. JevTalk retries with backoff (1, 2, 4,
8 and 16 seconds), so answers just get slower. If one call fails six times, you get an error and can ask again.

## Files

| File | What it is |
|---|---|
| `jevtalk.py` | Everything that talks to Jev: the spelling engine, the command line, the benchmark |
| `build_vocab.py` | Builds `vocab.txt` from wordfreq (run automatically on first use) |
| `jevtalk_app.py`, `app/index.html` | The app's local server and page |
| `JevTalkShell.swift`, `build_app.sh`, `jevtalk_icon.png` | The native Mac window, its build script and icon |
| `THIRD_PARTY_NOTICES.md` | Licenses for the icons, and a note on wordfreq |
| `LICENSE` | MIT |

## License

MIT, see [LICENSE](LICENSE). The Lucide icons are ISC/MIT, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
wordfreq's data isn't included; your own copy builds `vocab.txt` on your machine.

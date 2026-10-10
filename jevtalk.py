#!/usr/bin/env python3
"""JevTalk: make Jev (TypeSafe's System One model) answer in natural language.

Jev can't generate text. It only picks from options you give it and returns
probabilities. So we make it spell: every call shows Jev the answer so far
extended by each candidate next word, or by the next letter of a rarer word,
and Jev picks one. Ending the answer is one of the options, so stopping competes
with every next word. Jev also picks the answer's length and one key word up
front, several drafts are composed in parallel, and Jev picks the best finished
one.
"""
import argparse
import functools
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
VOCAB_FILE = Path(__file__).with_name("vocab.txt")
if not VOCAB_FILE.exists():  # not shipped (see build_vocab.py), so the first run builds it from wordfreq
    try:
        import build_vocab
    except ModuleNotFoundError as e:
        if e.name != "wordfreq":
            raise
        sys.exit("JevTalk builds its word list from wordfreq on first run. Install it, then try again:\n"
                 "    pip install wordfreq==3.1.1")
    print("First run: building vocab.txt from wordfreq…", file=sys.stderr)
    build_vocab.main()
VOCAB = VOCAB_FILE.read_text().split() + [str(n) for n in range(2101)]
WINDOW = 90  # options show at most this many trailing characters of the answer
PUNCT = ",."
stats = {"calls": 0, "cost": 0.0, "tokens": 0}
stats_lock = threading.Lock()


@functools.cache
def api_key():
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return key
    service = os.environ.get("JEVTALK_KEYCHAIN_SERVICE", "OPENROUTER_API_KEY")
    try:  # macOS keychain
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
    except FileNotFoundError:  # not a Mac
        sys.exit("Set OPENROUTER_API_KEY to your OpenRouter API key.")
    if out.returncode:
        sys.exit(f"No OPENROUTER_API_KEY set and no keychain item with service '{service}' "
                 "(set JEVTALK_KEYCHAIN_SERVICE to its name).")
    return out.stdout.strip()


def decide(state, questions):
    """One Jev request: a state plus named questions, answered in parallel."""
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(URL, body, headers), timeout=60) as r:
                resp = json.load(r)
            break
        except urllib.error.URLError as e:
            code = getattr(e, "code", None)
            if code in (400, 401, 402, 403, 404, 422) or attempt == 5:
                detail = e.read().decode()[:500] if code else e.reason
                raise RuntimeError(f"Jev request failed ({code}): {detail}") from None
            time.sleep(2 ** attempt)  # 429/529/5xx/network: back off and retry
    usage = resp.get("usage", {})
    with stats_lock:
        stats["calls"] += 1
        stats["cost"] += usage.get("cost", 0)
        stats["tokens"] += usage.get("input_tokens", 0)
    return resp["answers"]


@functools.cache
def proper_nouns():
    """lowercase -> Capitalized for words the system dictionary only knows capitalized (Paris, France)."""
    try:
        words = Path("/usr/share/dict/words").read_text().split()
    except OSError:
        return {}
    lower = {w for w in words if w.islower()}
    return {w.lower(): w for w in words if w[0].isupper() and w.lower() not in lower} | {"jev": "Jev"}


def pretty(text):
    """Sentence-case the lowercase internal text: capital starts, 'I', proper nouns."""
    caps = proper_nouns()
    text = re.sub(r"[a-z']+", lambda m: "I" + m[0][1:] if m[0] in ("i", "i'm", "i'll") else caps.get(m[0], m[0]), text)
    text = re.sub(r"\b(da|de|van|von|du|di) ([a-z])", lambda m: f"{m[1]} {m[2].upper()}", text)  # da Vinci
    return re.sub(r"(^|[.?!] )([a-z])", lambda m: m[1] + m[2].upper(), text)


def strings(x):
    """Every string inside nested dicts and lists."""
    if isinstance(x, str):
        return [x]
    items = x.values() if isinstance(x, dict) else x if isinstance(x, list) else []
    return [s for v in items for s in strings(v)]


def join(answer, piece):
    if not answer or piece[0] in PUNCT:
        return answer + piece
    return f"{answer} {piece}"


def show(answer, piece):
    text = pretty(join(answer, piece))
    return text if len(text) <= WINDOW else "…" + text[-WINDOW:]


IDENTITY = "Jev, a friendly assistant made by TypeSafe AI"
INSTRUCTIONS = ("Each option is a possible beginning of the answer to `question`. If an option ends with \"…\", "
                "its last word is cut off and continues with more letters; otherwise its last word is complete. "
                "An option ending with \".\" is a finished answer. "
                "Which option begins the best answer: correct, natural English that directly answers `question`?")
DONE = "Is `answer_so_far` already a complete answer to `question`, with nothing important left to add?"
MISTAKE = "Does `answer_so_far` contain a grammar mistake?"
BEST = "Which option is the best answer to `question`: correct, direct, fluent English, and concise?"
KEYWORD = ("Which word would be most useful to include in a correct, helpful answer to `question`? "
           "If an option ends with \"…\", the word is cut off and continues with more letters.")
LENGTH = "How many words long should an ideal answer to `question` be?"
LENGTHS = {"1 to 3 words": 3, "4 to 8 words": 8, "9 to 15 words": 15, "16 to 30 words": 30}
COMMON = set(VOCAB[:150])  # function words that may repeat freely
REPEAT_PENALTY = 0.3
DANGLING = set("a an the and or but of to my your our their his its with from by because than".split())
CONFIRM = 0.15  # re-ask a picked word when its letter group got at least this fraction of its probability


def choose(decide, state, options, ensemble, extra=None, instructions=INSTRUCTIONS):
    """Ask Jev to pick among `options` ({shown text: value}).

    Sends `ensemble` shuffled copies of the question in one request and averages
    them, which cancels Jev's position bias. `extra` questions ride along in the
    same request. Returns ({value: prob}, raw answers)."""
    questions = dict(extra or {})
    for i in range(ensemble):
        order = random.sample(list(options), len(options))
        questions[f"c{i}"] = {"type": "choice", "instructions": instructions, "criteria": dict.fromkeys(order)}
    answers = decide(state, questions)
    probs = dict.fromkeys(options.values(), 0.0)
    for i in range(ensemble):
        for text, p in answers[f"c{i}"]["probabilities"].items():
            probs[options[text]] += p / ensemble
    return probs, answers


def pick(probs, temperature):
    """Argmax at temperature 0, otherwise sample among options at least 20% as likely as the best (min-p)."""
    best = max(probs, key=probs.get)
    if not temperature:
        return best
    keys = [k for k, p in probs.items() if p >= 0.2 * probs[best]]
    return random.choices(keys, [probs[k] ** (1 / temperature) for k in keys])[0]


def next_piece(decide, state, answer, vocab, word_limit, shortlist, ensemble, stop_at, temperature, log, banned=(),
               instructions=INSTRUCTIONS):
    """(next word or ',', P(answer_so_far complete), P(answer_so_far has a grammar mistake)).

    The word is None when Jev says the answer is complete. `banned` words are not offered.

    The first question offers the `word_limit` most common words whole, plus one
    "first letter" option per group of the rest. Picking a letter narrows the
    vocabulary; letters are asked until at most `shortlist` words match, and then
    all of them are shown whole so rare words compete on equal footing.

    "The sky is blue be" can read as the start of "because", so when Jev picks a
    whole word while its first letter's group is close behind, it is asked once
    more: that word as is, or one of its longer extensions ("bec…")?"""
    used = re.findall(r"[a-z0-9']+", answer)
    prefix, done, mistake = "", None, None
    while True:
        cands = [w for w in vocab if w.startswith(prefix) and w not in banned]  # ponytail: linear scan, ~2ms
        if not prefix:
            words = [w for w in vocab[:word_limit] if w not in banned]
        elif len(cands) <= shortlist:
            words = cands
        else:  # letters only; the letters so far may already spell a word Jev hasn't been offered
            words = [prefix] if prefix in cands else []
        groups, whole = {}, set(words)
        for w in cands:
            if w not in whole:
                groups.setdefault(prefix + w[len(prefix)], []).append(w)
        options = {show(answer, w): ("word", w) for w in words if not used or w != used[-1]}  # no stammering
        for p, ws in groups.items():
            options[show(answer, ws[0] if len(ws) == 1 else p + "…")] = ("word", ws[0]) if len(ws) == 1 else ("prefix", p)
        extra = None
        if not prefix and answer:
            extra = {"done": {"type": "noul", "instructions": DONE}, "mistake": {"type": "noul", "instructions": MISTAKE}}
            if answer[-1] not in PUNCT and "," not in banned:
                options[show(answer, ",")] = ("word", ",")
            if answer[-1] not in PUNCT and used[-1] not in DANGLING:
                options[show(answer, ".")] = ("word", ".")  # stopping competes head to head with every next word
        if len(options) == 1 and not extra:
            (kind, value), = options.values()  # only one way forward: no need to ask
        else:
            probs, answers = choose(decide, state, options, ensemble, extra, instructions)
            if extra:
                done, mistake = answers["done"]["noul"], answers["mistake"]["noul"]
            if extra and done >= stop_at:
                log(f"  done? {done:.2f} mistake? {mistake:.2f} -> stop")
                return None, done, mistake
            for kv in probs:
                if kv[0] == "word" and kv[1] in used and kv[1] not in COMMON:
                    probs[kv] *= REPEAT_PENALTY
            ranked = sorted(probs.items(), key=lambda kv: -kv[1])
            log("  " + (f"done? {done:.2f} mistake? {mistake:.2f}  " if extra else "")
                + "  ".join(f"{v + ('…' if k == 'prefix' else '')!r} {p:.2f}" for (k, v), p in ranked[:4]))
            # sample only where whole words compete; a sampled letter would strand the draft among rare words
            kind, value = pick(probs, temperature if not prefix or not groups else 0)
            if (kind == "word" and not prefix and probs.get(("prefix", value[0]), 0) >= CONFIRM * probs[kind, value]
                    and any(w != value and w.startswith(value) for w in cands)):
                prefix = value  # the next question offers `value` whole against its extensions
                continue
        if value == ".":
            log("  '.' -> stop")
            return None, done, mistake
        if kind == "word":
            return value, done, mistake
        prefix = value


def compose(decide, state, vocab, budget, word_limit, shortlist, ensemble, stop_at, temperature, fix_at, log,
            on_text):
    """One draft answer, at most `budget` words, built one decision at a time.

    Every step also asks Jev whether the text so far has a grammar mistake; if
    so, the last word is taken back and banned at that spot."""
    state, pieces, bans, prefixes, backtracks = dict(state), [], {}, [], 0
    while True:
        answer = functools.reduce(join, pieces, "")
        if answer:
            state["answer_so_far"] = pretty(answer)
        piece, done, mistake = next_piece(decide, state, answer, vocab, word_limit, shortlist, ensemble, stop_at,
                                          temperature, log, bans.get(len(pieces), ()))
        if mistake is not None and mistake >= fix_at and backtracks < budget:
            backtracks += 1
            bad = pieces.pop()
            bans = {k: v for k, v in bans.items() if k <= len(pieces)}  # later bans were for another context
            bans.setdefault(len(pieces), set()).add(bad)
            log(f"  mistake? {mistake:.2f} -> take back {bad!r}")
            on_text(pretty(functools.reduce(join, pieces, "")))
            continue
        if answer:
            prefixes.append((done, answer))
        if piece is None:
            break
        if len(pieces) == budget:  # out of words: cut back to where Jev rated the answer most complete
            answer = max(prefixes)[1]
            log(f"  budget hit -> cut back to: {answer}")
            break
        pieces.append(piece)
        on_text(pretty(join(answer, piece)))
    answer = answer.rstrip(",")
    if answer and not answer.endswith("."):
        answer += "."
    return pretty(answer)


def polish(decide, state, text, ensemble, rounds, log, on_text=lambda s: None):
    """Let Jev fix small slips in a finished answer ("They make a honey."). Each
    round it picks the best of: the answer as is, every version with one word
    deleted, one common word inserted, or an article swapped. Stops when the
    answer itself wins."""
    for _ in range(rounds):
        words = text.lower().rstrip(".").split()
        fillers = VOCAB[:max(3, (250 - 3 * len(words)) // (len(words) + 1))]  # keep under Jev's 255 options
        variants = [words[:i] + words[i + 1:] for i in range(len(words))]
        variants += [words[:i] + [f] + words[i:] for i in range(len(words) + 1) for f in fillers]
        variants += [words[:i] + [a] + words[i + 1:] for i, w in enumerate(words) if w in ("a", "an", "the")
                     for a in ("a", "an", "the", "")]
        options = {text: text}
        for v in variants:
            v = " ".join(w for w in v if w).rstrip(",")
            if v:
                options[pretty(v + ".")] = pretty(v + ".")
        probs, _ = choose(decide, state, dict(list(options.items())[:255]), ensemble, instructions=BEST)
        best = max(probs, key=probs.get)
        log(f"  polish: {probs[best]:.2f} {best}  (as is: {probs[text]:.2f})")
        if best == text:
            break
        text = best
        on_text(text)
    return text


def reply(question, decide=decide, history=(), context=None, word_limit=40, shortlist=250, ensemble=3, max_words=40,
          stop_at=0.5, drafts=8, temperature=0.8, fix_at=0.75, polish_rounds=3, log=lambda s: None,
          on_text=lambda s: None, on_draft=lambda i, text, score=None: None):
    """Jev's answer to `question`. `history` is [(user, jev), ...]. `context` is extra
    state Jev sees (e.g. a document); its words become spellable but aren't offered whole.
    `on_text` streams the answer; `on_draft(i, text)` streams each draft as it's written,
    then `on_draft(i, final, score)` once Jev has rated the finished drafts.

    Jev first picks how long the answer should be (a hard word budget). Then
    `drafts` answers are composed in parallel (one greedy, the rest sampled)
    and Jev picks the best finished one, since judging whole texts is its forte."""
    from concurrent.futures import ThreadPoolExecutor

    seen = " ".join([IDENTITY, question, *(t for turn in history for t in turn)]).lower()
    vocab = list(dict.fromkeys(re.findall(r"[a-z0-9']+", seen) + VOCAB
                               + re.findall(r"[a-z0-9']+", " ".join(strings(context)).lower())))
    state = {"you_are": IDENTITY, "question": question, **(context or {})}
    if history:
        state["conversation"] = [{"user": u, "jev": j} for u, j in history]
    length, _ = choose(decide, state, {k: k for k in LENGTHS}, ensemble, instructions=LENGTH)
    budget = min(max_words, 2 * LENGTHS[max(length, key=length.get)])  # slack: drafts need room to finish
    log(f"  length plan: {max(length, key=length.get)} -> at most {budget} words")
    # core idea first: Jev spells the one word the answer most needs; draft 0 goes without it as a hedge
    asked = set(re.findall(r"[a-z0-9']+", seen))
    fresh = [w for w in VOCAB if w not in COMMON and w not in asked]
    key, _, _ = next_piece(decide, state, "", fresh, 0, shortlist, ensemble, 1, 0, log, instructions=KEYWORD)
    log(f"  key word: {key}")

    def draft(i):
        hinted = {**state, "key_word": key} if i or drafts == 1 else state

        def show(text):
            on_draft(i, text)
            if drafts == 1:
                on_text(text)
        return compose(decide, hinted, vocab, budget, word_limit, shortlist, ensemble, stop_at, temperature if i else 0,
                       fix_at, log if drafts == 1 else lambda s: None, show)

    with ThreadPoolExecutor(drafts) as pool:
        finished = list(pool.map(draft, range(drafts)))
    texts = [t for t in dict.fromkeys(finished) if t]
    if not texts:
        return ""
    best = {texts[0]: 1.0}
    if len(texts) > 1:
        best, _ = choose(decide, state, {t: t for t in texts}, ensemble, instructions=BEST)
        for t in sorted(texts, key=lambda t: -best[t]):
            log(f"  draft {best[t]:.2f}: {t}")
    for i, t in enumerate(finished):
        on_draft(i, t, best.get(t, 0.0))
    on_text(max(best, key=best.get))
    return polish(decide, state, max(best, key=best.get), ensemble, polish_rounds, log, on_text)


def selftest():
    """Offline check: a fake Jev that 'knows' the target answer must steer reply() to it exactly."""
    def fake(target, trap=None):  # trap: a wrong answer prefix Jev falls for once, then flags as a mistake
        def decide(state, questions):
            out = {}
            for name, q in questions.items():
                if q["type"] == "noul":
                    so_far = state.get("answer_so_far", "").lower()
                    out[name] = {"type": "noul", "noul": float(so_far == target if q["instructions"] == DONE
                                                               else so_far == trap)}
                    continue
                if q["instructions"] == KEYWORD:
                    out[name] = {"type": "choice", "probabilities": {o: 1.0 / len(q["criteria"]) for o in q["criteria"]}}
                    continue
                if q["instructions"] == LENGTH:
                    out[name] = {"type": "choice", "probabilities": {o: float(o == "16 to 30 words") for o in q["criteria"]}}
                    continue
                def fits(opt):
                    unfinished = opt.endswith("…")
                    opt = opt.rstrip("…").lower()
                    rest = target[len(opt):]
                    return target.startswith(opt) and (unfinished or not rest or not (rest[0].isalnum() or rest[0] == "'"))
                good = sorted((o for o in q["criteria"] if fits(o)), key=len)[-1:]  # most specific fit
                good = [o for o in q["criteria"] if o.lower() == trap] or good
                assert good or state.get("answer_so_far", "").lower() in (target, trap), list(q["criteria"])[:8]
                out[name] = {"type": "choice", "probabilities": {o: float(o in good) for o in q["criteria"]}}
            return out
        return decide

    for target, limit, short in [("paris is the capital of france", 40, 120), ("i think it's 42, maybe", 0, 1),
                                 ("photosynthesis, plants make sugar", 200, 255), ("the quixotic zeitgeist", 5, 3)]:
        got = reply(target.split(",")[0] + "?", decide=fake(target), word_limit=limit, shortlist=short,
                    drafts=1 + (limit == 5), temperature=0, polish_rounds=0)
        assert got == pretty(target) + ".", (got, target)
    got = reply("what do bees make?", decide=fake("they make honey", trap="they make a"), drafts=1, polish_rounds=0)
    assert got == "They make honey.", got
    print("selftest ok")


EVAL = ["What is the capital of France?", "Why is the sky blue?", "What do bees make?", "How are you today?",
        "What is 2 plus 2?", "Who wrote Romeo and Juliet?", "Give me a tip for sleeping better.",
        "What is your favorite color and why?", "What is the opposite of hot?", "Where do fish live?",
        "What should I eat for breakfast?", "Why do we need sleep?", "What color is grass?",
        "Is the earth round?", "What is a dog?", "Tell me a fun fact about octopuses."]
HELDOUT = ["What is the largest planet?", "How many legs does a spider have?", "What is ice made of?",
           "Why do leaves change color in the fall?", "What is your favorite food?", "How do I make a cup of tea?",
           "What language is spoken in Brazil?", "What do cows drink?", "Who painted the Mona Lisa?",
           "What is the boiling point of water in Celsius?", "Can penguins fly?", "What is a good name for a cat?",
           "Why do people yawn?", "What is the moon made of?", "How do plants get water?", "What is 10 minus 3?"]
JUDGE = {"correct": "Does `answer` correctly and directly answer `question`?",
         "fluent": "Is `answer` fluent, grammatical English with no repetition or nonsense?"}


def evaluate(questions=EVAL, **kw):
    """Answer every EVAL question concurrently, then have Jev grade each finished answer."""
    from concurrent.futures import ThreadPoolExecutor

    def run(q):
        a = reply(q, **kw)
        grades = decide({"question": q, "answer": a}, {k: {"type": "noul", "instructions": v} for k, v in JUDGE.items()})
        return q, a, {k: g["noul"] for k, g in grades.items()}

    started = time.time()
    with ThreadPoolExecutor(3) as pool:  # ponytail: 3 at a time stays under the ~1200 req/min limit
        results = list(pool.map(run, questions))
    for q, a, g in results:
        print(f"[correct {g['correct']:.2f} fluent {g['fluent']:.2f}] {q}\n    {a}")
    mean = {k: sum(g[k] for *_, g in results) / len(results) for k in JUDGE}
    print(f"MEAN correct {mean['correct']:.3f} fluent {mean['fluent']:.3f}  "
          f"({stats['calls']} calls, ${stats['cost']:.4f}, {time.time() - started:.0f}s)")


def main():
    ap = argparse.ArgumentParser(description="Make Jev answer questions by spelling, one decision at a time.")
    ap.add_argument("question", nargs="*", help="omit to chat interactively")
    ap.add_argument("--word-limit", type=int, default=40,
                    help="most common words offered whole at the first letter (0 = none)")
    ap.add_argument("--shortlist", type=int, default=250,
                    help="spell letters until at most this many words match, then offer them all whole "
                         "(with --word-limit 0 --shortlist 1, Jev spells every letter)")
    ap.add_argument("--ensemble", type=int, default=3, help="shuffled copies of each question (cancels position bias)")
    ap.add_argument("--max-words", type=int, default=40)
    ap.add_argument("--stop-at", type=float, default=0.5, help="stop when P(answer complete) reaches this")
    ap.add_argument("--drafts", type=int, default=8, help="answers composed in parallel; Jev picks the best one")
    ap.add_argument("--temperature", type=float, default=0.8, help="sampling temperature for drafts after the first")
    ap.add_argument("--fix-at", type=float, default=0.75,
                    help="take back the last word when P(grammar mistake so far) reaches this (>1 disables)")
    ap.add_argument("--polish", type=int, default=3, help="max rounds of Jev fixing small slips in the final answer")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every decision Jev makes")
    ap.add_argument("--selftest", action="store_true", help="run the offline check and exit")
    ap.add_argument("--eval", action="store_true", help="answer a fixed question set and have Jev grade it")
    ap.add_argument("--heldout", action="store_true", help="--eval on questions never used for tuning")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    opts = dict(word_limit=args.word_limit, shortlist=args.shortlist, ensemble=args.ensemble, max_words=args.max_words, stop_at=args.stop_at,
                drafts=args.drafts, temperature=args.temperature, fix_at=args.fix_at,
                polish_rounds=args.polish)
    if args.eval or args.heldout:
        return evaluate(HELDOUT if args.heldout else EVAL, **opts)

    def ask(question, history=()):
        printed = [""]

        def stream(text):  # backspaces over taken-back words
            keep = len(os.path.commonprefix([printed[0], text]))
            print("\b \b" * (len(printed[0]) - keep) + text[keep:], end="", flush=True)
            printed[0] = text

        started = time.time()
        stats.update(calls=0, cost=0.0, tokens=0)
        print("jev> ", end="", flush=True)
        log = (lambda s: print(f"\n\033[2m{s}\033[0m", file=sys.stderr, flush=True)) if args.verbose else (lambda s: None)
        answer = reply(question, history=history, log=log, on_text=stream, **opts)
        print(answer[len(printed[0]):] if answer.startswith(printed[0]) else f"\n  ↳ cut back to: {answer}", flush=True)
        print(f"\033[2m[{stats['calls']} Jev calls, {stats['tokens']:,} input tokens, ${stats['cost']:.4f}, "
              f"{time.time() - started:.1f}s]\033[0m", file=sys.stderr)
        return answer

    if args.question:
        ask(" ".join(args.question))
        return
    history = []
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if question:
            history.append((question, ask(question, history)))


if __name__ == "__main__":
    main()

"""Build config/cluster_ignore_words.json (1000 ignore words per language).

Uses OpenSubtitles frequency lists (hermitdave/FrequencyWords) so the most
common everyday words are dropped before clustering. Adds news/URL noise and
ASCII foldings for Danish characters.
"""

from __future__ import annotations

import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "config", "cluster_ignore_words.json")

FREQ_URLS = {
    "en": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_50k.txt",
    "da": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/da/da_50k.txt",
}

STOP_ISO_URLS = {
    "en": "https://raw.githubusercontent.com/stopwords-iso/stopwords-en/master/stopwords-en.txt",
    "da": "https://raw.githubusercontent.com/stopwords-iso/stopwords-da/master/stopwords-da.txt",
}

TARGET = 1000

NEWS_URL_NOISE = """
www http https html htm php asp aspx index page pages com org net edu gov
co uk us eu dk se no fi de fr nl amp amphtml
news new latest live video videos photo photos gallery article articles
story stories report reports update updates breaking opinion analysis
feature features comment comments editorial editorials newsletter podcast
subscribe share shared sharing click read more less full story content
international internationalt national nationalt politics political
nyhed nyheder seneste artikel artikler indland udland sport kultur politik
krimi debat penge erhverv erhvervs underholdning forbrug vejret trafik
liveblog sektion sektioner tema temaer samfund samfunds
business world lifestyle health science tech technology entertainment
media press reuters afp tv2 dr bt
danmark denmark danish dansk danske danmarks
kobenhavn københavn koebenhavn copenhagen aarhus århus aalborg
aarig aarige arig arige year old years
ece art id cid fp exp alg
minut minutter time timer dag dage uge uger maaned maaneder måned måneder
aar år aaret året idag i dag igaar igår
guvernoer guvenør
""".split()

DA_EXTRA = """
ad af aldrig alle alt andet at bag bare begyndt begynder begyndte blandt
blev blive bliver både både baade da de dem den denne dens der derefter
deres derfor derfra deri dermed dernæst dernæst dernæste dersom dertil
desuden det dette dig din dine dit dog du efter egen eget egne eller
ellers end endnu en et far flere fleste for foran fordi forrige fra før
foer gennem gid gik gjorde gøre gør gøre gøre gøre gøre gøre gøre gøre gøre
gør gør gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre
gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre gøre
ha hadede havde har have hele hellere hen hende hendes henholdsvis her
hos hun hvad hvem hver hvert hvilke hvilken hvilket hvis hvor hvordan
hvorefter hvorfor hvorfra hvori hvormed hvorved i ifølge ifoelge igen
igennem igår igaar ihvertfald ikke imens imod ind inden indenfor
indtil ingen intet ja jeg jer jeres jo kan kom komme kommer kommet kun
kunde kunne lang langt lidt lige ligesom lille løs løse man mange
mange med meget mellem men mens mere mest mig min mine mit mod må maa
måtte maatte ned nej nemlig nogensinde noget nogen nogle nok nu når
naar og også ogsaa okay om omkring op os over overalt på pa samme
sammen selv selve selvom ser set sig sige siger sin sine sit skal
skulle snart som stadig stor store stort sådan saadan så saa tage tager
taget thi ti til tilbage tit to tre tor torde ud ude uden under untagen
var ved vi via vil ville være vaere været vaeret væsentlig ægte øvrig
øvrige øvrigt øvrigt øvrige øvrigt øvrige øvrigt øvrige øvrigt øvrige
allerede alligevel almindelig almindelige altså altid andetsteds
antagelig omkring cirka cirka cirka cirka cirka cirka cirka cirka cirka
cirka cirka cirka cirka cirka cirka cirka cirka cirka cirka cirka cirka
""".split()


def _norm_token(raw: str) -> str:
    w = (raw or "").strip().casefold()
    if not w:
        return ""
    chars = []
    for ch in w:
        if ch.isalpha() or ch in "æøåäöüéœæ":
            chars.append(ch)
    w = "".join(chars)
    return w if len(w) >= 2 else ""


def _ascii_folds(word: str) -> list[str]:
    out = [word]
    folded = (
        word.replace("æ", "ae")
        .replace("ø", "oe")
        .replace("å", "aa")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
    )
    if folded != word:
        out.append(folded)
    # common Danish URL-style single-letter swaps
    alt = word.replace("æ", "a").replace("ø", "o").replace("å", "a")
    if alt != word and alt not in out:
        out.append(alt)
    return out


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8")


def frequency_words(text: str, limit: int) -> list[str]:
    words: list[str] = []
    seen = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        raw = line.split()[0]
        w = _norm_token(raw)
        if not w or w in seen:
            continue
        seen.add(w)
        words.append(w)
        if len(words) >= limit:
            break
    return words


def stop_iso_words(text: str) -> list[str]:
    words = []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        w = _norm_token(line)
        if not w or w in seen:
            continue
        # skip pure digit-like leftovers
        if w.isdigit():
            continue
        seen.add(w)
        words.append(w)
    return words


def build_lang_list(lang: str) -> list[str]:
    freq = frequency_words(fetch_text(FREQ_URLS[lang]), TARGET * 2)
    iso = stop_iso_words(fetch_text(STOP_ISO_URLS[lang]))

    forced: list[str] = []
    for raw in NEWS_URL_NOISE + (DA_EXTRA if lang == "da" else []):
        w = _norm_token(raw)
        if w:
            forced.append(w)

    # Prefer forced + frequency order, then fill from iso.
    ordered: list[str] = []
    seen = set()

    def add(word: str) -> None:
        for variant in _ascii_folds(word):
            v = _norm_token(variant)
            if not v or v in seen:
                continue
            seen.add(v)
            ordered.append(v)

    for w in forced:
        add(w)
    for w in freq:
        add(w)
        if len(ordered) >= TARGET:
            break
    if len(ordered) < TARGET:
        for w in iso:
            add(w)
            if len(ordered) >= TARGET:
                break

    # Exact TARGET: trim or pad from remaining iso/freq
    if len(ordered) > TARGET:
        ordered = ordered[:TARGET]
    elif len(ordered) < TARGET:
        for w in iso + freq:
            add(w)
            if len(ordered) >= TARGET:
                break
        ordered = ordered[:TARGET]
    return ordered


def main() -> None:
    payload = {
        "description": (
            "Ignore words for headline clustering. After these are removed from "
            "titles/URLs, remaining tokens are used to group related articles. "
            "Approximately 1000 words per language (frequency-based + news/URL noise)."
        ),
        "en": build_lang_list("en"),
        "da": build_lang_list("da"),
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote {OUT_PATH}")
    print(f"  en: {len(payload['en'])} words")
    print(f"  da: {len(payload['da'])} words")
    for probe in ("russia", "ukraine", "rusland", "trump", "the", "ikke", "nyheder"):
        print(
            f"  {probe!r}: en={probe in payload['en']} da={probe in payload['da']}"
        )


if __name__ == "__main__":
    main()

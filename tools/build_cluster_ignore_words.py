"""Build config/cluster_ignore_words.json (1000 ignore words per language).

Uses OpenSubtitles frequency lists (hermitdave/FrequencyWords) so the most
common everyday words are dropped before clustering. Adds news/URL noise and
ASCII foldings for Scandinavian characters.
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
    "sv": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/sv/sv_50k.txt",
    "no": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/no/no_50k.txt",
}

STOP_ISO_URLS = {
    "en": "https://raw.githubusercontent.com/stopwords-iso/stopwords-en/master/stopwords-en.txt",
    "da": "https://raw.githubusercontent.com/stopwords-iso/stopwords-da/master/stopwords-da.txt",
    "sv": "https://raw.githubusercontent.com/stopwords-iso/stopwords-sv/master/stopwords-sv.txt",
    "no": "https://raw.githubusercontent.com/stopwords-iso/stopwords-no/master/stopwords-no.txt",
}

TARGET = 1000

# Shared URL/tech/news noise (all languages).
NEWS_URL_NOISE = """
www http https html htm php asp aspx index page pages com org net edu gov
co uk us eu dk se no fi de fr nl amp amphtml
news new latest live video videos photo photos gallery article articles
story stories report reports update updates breaking opinion analysis
feature features comment comments editorial editorials newsletter podcast
subscribe share shared sharing click read more less full story content
international national politics political business world lifestyle health
science tech technology entertainment media press reuters afp tt ntb
year old years min minute minutes hour hours day days week weeks month months
""".split()

DA_NOISE = """
nyhed nyheder seneste artikel artikler indland udland sport kultur politik
krimi debat penge erhverv erhvervs underholdning forbrug vejret trafik
liveblog sektion sektioner tema temaer samfund samfunds
danmark denmark danish dansk danske danmarks
kobenhavn københavn koebenhavn copenhagen aarhus århus aalborg
ece art id cid fp exp alg
minut minutter time timer dag dage uge uger maaned maaneder måned måneder
aar år aaret året idag igaar igår
tv2 dr bt
""".split()

SV_NOISE = """
nyhet nyheter senaste artikel artiklar inland utland sport kultur politik
kriminal brott debatt ekonomi naringsliv näringsliv underhallning underhållning
vader väder trafik ledare ledareanalys direkt liveblog sektion sektioner
tema teman samhalle samhälle
sverige swedish svensk svenska sveriges stockholm goteborg göteborg malmo malmö
aftonbladet expressen svd dn svt omni gp di tv4
minut minuter timme timmar dag dagar vecka veckor manad månad manader månader
ar år aret året idag igar igår
justnu just nu
""".split()

NO_NOISE = """
nyhet nyheter siste artikkel artikler innenriks utenriks sport kultur politikk
kriminalitet debatt okonomi økonomi naringsliv næringsliv underholdning
vaer vær trafikk leder kommentar direkte liveblog seksjon seksjoner
tema temaer samfunn
norge norway norwegian norsk norske norges oslo bergen trondheim stavanger
vg nrk dagbladet aftenposten nettavisen e24 tv2 adressa
minutt minutter time timer dag dager uke uker maned måned maneder måneder
ar år aret året idag igar igår
nett nettavisen
""".split()

DA_EXTRA = """
ad af aldrig alle alt andet at bag bare begyndt begynder begyndte blandt
blev blive bliver både baade da de dem den denne dens der derefter
deres derfor derfra deri dermed dernæst dersom dertil desuden det dette
dig din dine dit dog du efter egen eget egne eller ellers end endnu en et
far flere fleste for foran fordi forrige fra før foer gennem gid gik
gjorde gøre gør ha hadede havde har have hele hellere hen hende hendes
her hos hun hvad hvem hver hvert hvilke hvilken hvilket hvis hvor hvordan
hvorfor hvorfra hvori i ifølge ifoelge igen igennem igår igaar ikke imens
imod ind inden indenfor indtil ingen intet ja jeg jer jeres jo kan kom
komme kommer kommet kun kunde kunne lang langt lidt lige ligesom lille
man mange med meget mellem men mens mere mest mig min mine mit mod må maa
måtte maatte ned nej noget nogen nogle nok nu når naar og også ogsaa om
omkring op os over på pa samme sammen selv selve selvom ser set sig sige
siger sin sine sit skal skulle snart som stadig stor store stort sådan
saadan så saa tage tager taget til tilbage to tre ud ude uden under var
ved vi via vil ville være vaere været vaeret allerede alligevel altså
altid cirka
""".split()

SV_EXTRA = """
aderton aldrig alla allt alltid alltså andra andra andras annat att av
bara bland blev bli blir blivit både båda de dem den denna dens deras
dess dessa det detta dig din dina ditt du där då efter ej eller en ett
eftersom emot för från genom gick gjorde gjort han hans har hade honom
henne hennes hon hur i ifall igen icke ingen ingenting inget innan
inne inte ja jag ju kan kunde man med mellan men mer mest mig min mina
mitt mot mycket ni nu när någon något några och om oss på samma sedan
sig sin sina sitt själv skulle som sådan sådana sådant till under upp
ut utan vad var vara varje varken varför vart vem vi vid vilka vilken
vilket vill vore år även över
""".split()

NO_EXTRA = """
alle andre arbeid at av bare begge ble blei bli blir blitt både båe da
de deg dei deim deira deires dem den denne der dere deres det dette di
din disse ditt du dykk dykkar eg ein eit eitt eller elles en enn er et
ett etter for fordi fra før ha hadde han hans har hennar henne hennes
her hjå ho hoe honom hoss hossen hun hva hvem hver hvilke hvilken hvis
hvor hvordan hvorfor i ikke ikkje ingen ingi ingenting inkje inn inni
ja jeg kan kom korleis korso kun kunne kva kvar kvarhelst kven kvi
kvifor man mange me med medan meg meget mellom men mi min mine mitt
mot mykje ned no noe noen nok noka noko nokon nokor nokre nu nå når
og også om opp oss over på samme seg selv si sia sidan siden sin sine
sitt sjøl skal skulle slik so som somme somt så sånn til um upp ut
uten var vart varte ved vere verte vi vil ville vore vorte vår være
vært å
""".split()

LANG_EXTRAS = {
    "da": DA_NOISE + DA_EXTRA,
    "sv": SV_NOISE + SV_EXTRA,
    "no": NO_NOISE + NO_EXTRA,
    "en": [],
}


def _norm_token(raw: str) -> str:
    w = (raw or "").strip().casefold()
    if not w:
        return ""
    chars = []
    for ch in w:
        if ch.isalpha() or ch in "æøåäöüéœ":
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
    alt = (
        word.replace("æ", "a")
        .replace("ø", "o")
        .replace("å", "a")
        .replace("ä", "a")
        .replace("ö", "o")
    )
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
        if w.isdigit():
            continue
        seen.add(w)
        words.append(w)
    return words


def build_lang_list(lang: str) -> list[str]:
    freq = frequency_words(fetch_text(FREQ_URLS[lang]), TARGET * 2)
    iso = stop_iso_words(fetch_text(STOP_ISO_URLS[lang]))

    forced: list[str] = []
    for raw in NEWS_URL_NOISE + LANG_EXTRAS.get(lang, []):
        w = _norm_token(raw)
        if w:
            forced.append(w)

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
    langs = ("en", "da", "sv", "no")
    payload = {
        "description": (
            "Ignore words for headline clustering. After these are removed from "
            "titles/URLs, remaining tokens are used to group related articles. "
            "Approximately 1000 words per language (frequency-based + news/URL noise)."
        ),
    }
    for lang in langs:
        payload[lang] = build_lang_list(lang)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote {OUT_PATH}")
    for lang in langs:
        print(f"  {lang}: {len(payload[lang])} words")
    for probe in (
        "russia",
        "ukraine",
        "rusland",
        "ryssland",
        "trump",
        "the",
        "ikke",
        "inte",
        "nyheter",
        "sverige",
        "norge",
    ):
        bits = " ".join(f"{lang}={probe in payload[lang]}" for lang in langs)
        print(f"  {probe!r}: {bits}")


if __name__ == "__main__":
    main()

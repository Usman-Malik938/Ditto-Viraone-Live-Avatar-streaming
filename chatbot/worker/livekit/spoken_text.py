"""Rewrites the reply text that goes to the voice (never the text the visitor reads, the history or the knowledge base)
so ElevenLabs speaks it the way a Polish speaker would:
- prices and numbers as Polish words, with the right form of "złoty" ("dwieście pięćdziesiąt złotych"), and after
  "od", "do", "w ciągu", "około" ... in the genitive ("od sześciuset złotych");
- number ranges as "od jednego do dwóch miesięcy" ("w 1-2 miesiące" becomes "w ciągu jednego do dwóch miesięcy");
- phone numbers digit by digit ("siedem, trzy, siedem, ...");
- website and e-mail addresses phonetically ("polster kropka pe el", "es cztery u kropka a gie");
- the brand SCARD as "Eskard".
Only Polish is rewritten; other languages pass through unchanged."""
import re

UNITS = ["zero", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć"]
TEENS = ["dziesięć", "jedenaście", "dwanaście", "trzynaście", "czternaście", "piętnaście", "szesnaście",
         "siedemnaście", "osiemnaście", "dziewiętnaście"]
TENS = ["", "", "dwadzieścia", "trzydzieści", "czterdzieści", "pięćdziesiąt", "sześćdziesiąt", "siedemdziesiąt",
        "osiemdziesiąt", "dziewięćdziesiąt"]
HUNDREDS = ["", "sto", "dwieście", "trzysta", "czterysta", "pięćset", "sześćset", "siedemset", "osiemset",
            "dziewięćset"]

UNITS_GENITIVE = ["zera", "jednego", "dwóch", "trzech", "czterech", "pięciu", "sześciu", "siedmiu", "ośmiu",
                  "dziewięciu"]
TEENS_GENITIVE = ["dziesięciu", "jedenastu", "dwunastu", "trzynastu", "czternastu", "piętnastu", "szesnastu",
                  "siedemnastu", "osiemnastu", "dziewiętnastu"]
TENS_GENITIVE = ["", "", "dwudziestu", "trzydziestu", "czterdziestu", "pięćdziesięciu", "sześćdziesięciu",
                 "siedemdziesięciu", "osiemdziesięciu", "dziewięćdziesięciu"]
HUNDREDS_GENITIVE = ["", "stu", "dwustu", "trzystu", "czterystu", "pięciuset", "sześciuset", "siedmiuset", "ośmiuset",
                     "dziewięciuset"]

# Big units: (value, singular, few (2-4), many) in the nominative; the genitive of the plural is the "many" form.
SCALES = [(1_000_000, "milion", "miliony", "milionów", "miliona"), (1_000, "tysiąc", "tysiące", "tysięcy", "tysiąca")]

LETTER_NAMES = {"a": "a", "b": "be", "c": "ce", "d": "de", "e": "e", "f": "ef", "g": "gie", "h": "ha", "i": "i",
                "j": "jot", "k": "ka", "l": "el", "m": "em", "n": "en", "o": "o", "p": "pe", "q": "ku", "r": "er",
                "s": "es", "t": "te", "u": "u", "v": "fał", "w": "wu", "x": "iks", "y": "igrek", "z": "zet"}
# Domain endings read as a word rather than letter by letter.
SPOKEN_TOP_LEVEL_DOMAINS = {"ag": "agie", "com": "kom", "net": "net", "org": "org", "info": "info", "online": "onlajn"}
# Address parts with a fixed Polish reading (the client asked for "wideo kropka es cztery u kropka agie").
SPOKEN_LABELS = {"www": "wu wu wu", "scard": "eskard", "video": "wideo"}
TOP_LEVEL_DOMAINS = r"(?:pl|com|ag|ai|io|eu|net|org|de|uk|app|dev|info|biz|co|me|online|store|shop|tv)"

# Words after which Polish puts a number (and the noun after it) in the genitive.
GENITIVE_TRIGGERS = r"(?:od|do|około|ok\.|ponad|powyżej|poniżej|blisko|prawie|bez|w ciągu|w okolicach|w wysokości)"
CURRENCY = r"(?:zł|PLN|złotych|złote|złoty|złotego)"
# Nouns of time a range is usually about, with their genitive plural.
GENITIVE_PLURAL = {"miesiące": "miesięcy", "miesiąca": "miesięcy", "miesiąc": "miesięcy", "miesięcy": "miesięcy",
                   "tygodnie": "tygodni", "tygodnia": "tygodni", "tydzień": "tygodni", "tygodni": "tygodni",
                   "dni": "dni", "dzień": "dni", "dnia": "dni", "godziny": "godzin", "godzina": "godzin",
                   "godzin": "godzin", "lata": "lat", "rok": "lat", "lat": "lat", "minuty": "minut", "minut": "minut",
                   "zł": "złotych", "zł.": "złotych", "PLN": "złotych", "złote": "złotych", "złotych": "złotych"}
# Feminine nouns take "dwie" rather than "dwa".
FEMININE_NOUN_STARTS = ("stron", "godzin", "osob", "osób", "wersj", "rolk", "rolek", "kampani", "minut", "sekund",
                        "podstron", "grafik", "reklam", "opcj", "wizytówk", "karty", "kart", "lekcj", "sesj", "zmian")

# Number as the first part of a compound adjective: "30-sekundowa" -> "trzydziestosekundowa".
UNIT_PREFIXES = ["", "jedno", "dwu", "trzy", "cztero", "pięcio", "sześcio", "siedmio", "ośmio", "dziewięcio"]
TEEN_PREFIXES = ["dziesięcio", "jedenasto", "dwunasto", "trzynasto", "czternasto", "piętnasto", "szesnasto",
                 "siedemnasto", "osiemnasto", "dziewiętnasto"]
TENS_PREFIXES = ["", "", "dwudziesto", "trzydziesto", "czterdziesto", "pięćdziesięcio", "sześćdziesięcio",
                 "siedemdziesięcio", "osiemdziesięcio", "dziewięćdziesięcio"]
# Abbreviations end in a full stop, which the voice treats as the end of a sentence: a pause and a fresh intonation in
# the middle of what is one sentence. They are written out instead.
ABBREVIATIONS = [(r"\bnp\.", "na przykład"), (r"\bm\.in\.", "między innymi"), (r"\bitd\.", "i tak dalej"),
                 (r"\bitp\.", "i tym podobne"), (r"\btj\.", "to jest"), (r"\btzw\.", "tak zwany"),
                 (r"\btel\.", "telefon"), (r"\bul\.", "ulica"), (r"\bgodz\.", "godzina"),
                 (r"\bok\.(?=\s*\d)", "około"), (r"\bSp\.(?=\s*z\b)", "spółka"),
                 (r"\bz o\.\s?o\.", "z ograniczoną odpowiedzialnością"), (r"(?<=\d)\s*r\.(?=\s)", " roku")]
NUMBER = r"\d{1,3}(?:[  ]\d{3})+|\d+"
# A number that isn't part of a word, a time (17:00), a date (16.11.2026) or a decimal written with a dot.
STANDALONE = r"(?<![\w.,:/@])(?:{number})(?![\w:]|[.,]\d)"


def plural_form(number, one, few, many):
    if number == 1:
        return one
    if number % 10 in (2, 3, 4) and number % 100 not in (12, 13, 14):
        return few
    return many


def words_below_thousand(number, genitive=False):
    hundreds_table, tens_table, teens_table, units_table = (
        (HUNDREDS_GENITIVE, TENS_GENITIVE, TEENS_GENITIVE, UNITS_GENITIVE) if genitive
        else (HUNDREDS, TENS, TEENS, UNITS))
    words = []
    hundreds, rest = divmod(number, 100)
    if hundreds:
        words.append(hundreds_table[hundreds])
    if 10 <= rest <= 19:
        words.append(teens_table[rest - 10])
    else:
        tens, units = divmod(rest, 10)
        if tens:
            words.append(tens_table[tens])
        if units:
            words.append(units_table[units])
    return words


def number_in_words(number, genitive=False):
    """Polish cardinal number, nominative or genitive: 250 -> "dwieście pięćdziesiąt", genitive "dwustu pięćdziesięciu"."""
    if number == 0:
        return UNITS_GENITIVE[0] if genitive else UNITS[0]
    words = []
    for scale, one, few, many, one_genitive in SCALES:
        count, number = divmod(number, scale)
        if not count:
            continue
        if count == 1:
            words.append(one_genitive if genitive else one)
        else:
            words.extend(words_below_thousand(count, genitive))
            words.append(many if genitive else plural_form(count, one, few, many))
    words.extend(words_below_thousand(number, genitive))
    return " ".join(words)


def parse_number(text):
    return int(re.sub(r"[  ]", "", text))


def zloty_form(number, genitive):
    if genitive:
        return "złotego" if number == 1 else "złotych"
    return plural_form(number, "złoty", "złote", "złotych")


def grosz_form(number, genitive):
    if genitive:
        return "grosza" if number == 1 else "groszy"
    return plural_form(number, "grosz", "grosze", "groszy")


def digits_one_by_one(digits):
    return ", ".join(UNITS[int(digit)] for digit in digits)


def spell_label(label):
    """A domain part with digits or very short is spelled out: "s4u" -> "es cztery u", "pl" -> "pe el"."""
    spoken = []
    for character in label.lower():
        if character.isdigit():
            spoken.append(UNITS[int(character)])
        elif character in LETTER_NAMES:
            spoken.append(LETTER_NAMES[character])
        elif character == "-":
            spoken.append("myślnik")
    return " ".join(spoken)


def speak_domain_label(label):
    lowered = label.lower()
    if lowered in SPOKEN_LABELS:
        return SPOKEN_LABELS[lowered]
    if len(lowered) <= 2:
        return spell_label(lowered)
    # Letters and digits mixed ("smart4u", "s4u"): words of 3+ letters stay words, the rest is spelled.
    parts = []
    for run in re.findall(r"[a-z]+|\d+|-", lowered):
        parts.append(run if run.isalpha() and len(run) >= 3 else spell_label(run))
    return " ".join(parts)


def speak_address(address):
    """polster.pl -> "polster kropka pe el"; https://www.s4u.ag/kontakt -> "wu wu wu kropka es cztery u kropka a gie
    ukośnik kontakt"."""
    address = re.sub(r"^https?://", "", address, flags=re.IGNORECASE)
    host, _, path = address.partition("/")
    labels = host.split(".")
    spoken_labels = [speak_domain_label(label) for label in labels[:-1]]
    top_level = labels[-1].lower()
    spoken_labels.append(SPOKEN_TOP_LEVEL_DOMAINS.get(top_level) or spell_label(top_level))
    spoken = ", kropka, ".join(spoken_labels)
    for segment in [segment for segment in path.split("/") if segment]:
        spoken += ", ukośnik, " + segment.replace("-", " ")
    return spoken


def speak_email(match):
    return f"{match.group(1)}, małpa, {speak_address(match.group(2))}"


def speak_phone(match):
    country_code, digits = match.group(1), re.sub(r"\D", "", match.group(2))
    spoken = digits_one_by_one(digits)
    if country_code:
        country_number = int(re.sub(r"\D", "", country_code))
        spoken = f"plus {number_in_words(country_number)}, {spoken}"
    return spoken


def speak_range(match):
    """1-2 miesiące / 1–2 miesięcy / 250-500 zł -> "od jednego do dwóch miesięcy"; after "w" it becomes "w ciągu"."""
    preposition, first, second, noun = match.group(1), match.group(2), match.group(3), match.group(4)
    words = f"{number_in_words(parse_number(first), True)} do {number_in_words(parse_number(second), True)}"
    noun_text = ""
    if noun:
        noun_text = " " + GENITIVE_PLURAL.get(noun, GENITIVE_PLURAL.get(noun.lower(), noun))
    if preposition and preposition.strip().lower() == "w":
        return f"w ciągu {words}{noun_text}"
    if preposition and preposition.strip().lower() == "od":
        return f"{preposition}{words}{noun_text}"
    return f"{preposition or ''}od {words}{noun_text}"


def speak_money(match):
    preposition, whole, fraction = match.group(1), match.group(2), match.group(3)
    genitive = bool(preposition)
    amount = parse_number(whole)
    spoken = f"{number_in_words(amount, genitive)} {zloty_form(amount, genitive)}"
    if fraction and int(fraction):
        cents = int(fraction.ljust(2, "0"))
        spoken += f" {number_in_words(cents, genitive)} {grosz_form(cents, genitive)}"
    return (preposition or "") + spoken


def speak_percent(match):
    whole, fraction = match.group(1), match.group(2)
    spoken = number_in_words(parse_number(whole))
    if fraction:
        spoken += " przecinek " + " ".join(UNITS[int(digit)] for digit in fraction)
    return spoken + " procent"


def speak_plain_number(match):
    preposition, whole, noun = match.group(1), match.group(2), match.group(3) or ""
    number = parse_number(whole)
    spoken = number_in_words(number, genitive=bool(preposition))
    if not preposition and noun.lower().startswith(FEMININE_NOUN_STARTS) and number % 10 == 2 and number % 100 != 12:
        spoken = spoken[: -len("dwa")] + "dwie"
    return (preposition or "") + spoken + (" " + noun if noun else "")


def compound_prefix(number):
    """The number as the first part of a compound word: 30 -> "trzydziesto", 45 -> "czterdziestopięcio"."""
    if number == 100:
        return "stu"
    if 10 <= number <= 19:
        return TEEN_PREFIXES[number - 10]
    if 1 <= number <= 99:
        tens, units = divmod(number, 10)
        return TENS_PREFIXES[tens] + UNIT_PREFIXES[units]
    return number_in_words(number) + " "


def write_out_abbreviations(text):
    for pattern, replacement in ABBREVIATIONS:
        def keep_capital(match, replacement=replacement):
            if match.group(0)[:1].isupper():
                return replacement[:1].upper() + replacement[1:]
            return replacement
        text = re.sub(pattern, keep_capital, text, flags=re.IGNORECASE)
    return text


def say_twice(spoken, key, said):
    """Contact details are said once more, with short pauses, so the visitor can note them down. Once per answer."""
    if said is None or key in said:
        return spoken
    said.add(key)
    return f"{spoken}, powtarzam, {spoken}"


def speak_polish(text, said=None):
    """The whole rewrite for one piece of reply text. The order matters: addresses and phone numbers first, so their
    digits aren't read as amounts. said: contact details already repeated in this answer (None: never repeat)."""
    def email(match):
        return say_twice(speak_email(match), match.group(0).lower(), said)

    def address(match):
        return say_twice(speak_address(match.group(0)), match.group(0).lower().rstrip("/"), said)

    def phone(match):
        # Company registry numbers (NIP, KRS, REGON) are read digit by digit too, but they aren't contact details.
        if re.search(r"\b(?:NIP|KRS|REGON)\b\W*$", match.string[max(0, match.start() - 12):match.start()]):
            return speak_phone(match)
        return say_twice(speak_phone(match), re.sub(r"\D", "", match.group(0))[-9:], said)

    text = re.sub(r"\b([\w.+-]+)@([\w-]+(?:\.[\w-]+)*\.[a-z]{2,6})\b", email, text, flags=re.IGNORECASE)
    text = re.sub(rf"(?<![@\w])((?:https?://)?(?:[a-z0-9-]+\.)+{TOP_LEVEL_DOMAINS})(/[\w/-]*)?(?![\w])",
                  address, text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![\w])(\+\d{2}[  -]?)?(\d{3}[  -]?\d{3}[  -]?\d{3})(?![\w]|[  -]?\d)", phone, text)
    text = re.sub(r"(?<![\w])(\+\d{2})?(\d{9,11})(?![\w])", phone, text)
    text = re.sub(r"\bS-?CARD(?=\b|[a-ząęółśżźćń-])-?", "Eskard", text, flags=re.IGNORECASE)
    text = write_out_abbreviations(text)
    text = re.sub(r"(?<![\w/])24\s*/\s*7(?![\w/])", "dwadzieścia cztery na siedem", text)
    text = re.sub(r"(?<![\w.,:/])(\d{1,3})-(?=[a-ząęółśżźćń]{3,})",
                  lambda match: compound_prefix(int(match.group(1))), text, flags=re.IGNORECASE)
    preposition = rf"((?:\b{GENITIVE_TRIGGERS}|\bw)\s+)?"
    text = re.sub(rf"{preposition}(?<![\w.,:/])({NUMBER})\s*[-–—]\s*({NUMBER})(?![\w:]|[.,]\d)(?:\s+({CURRENCY}|[a-ząęółśżźćń]+))?",
                  speak_range, text, flags=re.IGNORECASE)
    trigger = rf"(\b{GENITIVE_TRIGGERS}\s+)?"
    text = re.sub(rf"{trigger}(?<![\w.,:/])({NUMBER})(?:,(\d{{1,2}}))?\s*{CURRENCY}(?=$|[\s.,;:!?)/])",
                  speak_money, text, flags=re.IGNORECASE)
    text = re.sub(r"/\s*(?:mies\.|miesiąc|mc|m-c)", " miesięcznie", text, flags=re.IGNORECASE)
    text = re.sub(rf"(?<![\w.,:/])({NUMBER})(?:,(\d+))?\s*%", speak_percent, text)
    text = re.sub(rf"{trigger}{STANDALONE.format(number='(' + NUMBER + ')')}(?:\s+([a-ząęółśżźćń]+))?",
                  speak_plain_number, text, flags=re.IGNORECASE)
    return text


class SpokenTextRewriter:
    """Rewrites a streamed reply piece by piece. Text is passed on only up to a point where nothing that follows can
    change it: a number, address or "od"/"do" at the end is held until the next words arrive."""

    HOLD_BACK = re.compile(rf"(?:\S*[\d@/+%]\S*|\S*\.\S+|{GENITIVE_TRIGGERS}|w|z|sp|ok|nip|krs|regon|ciągu|okolicach|wysokości|s-?card\S*)",
                           re.IGNORECASE)

    def __init__(self, language_code):
        self.enabled = language_code == "pl"
        self.buffer = ""
        self.said_contacts = set()

    def feed(self, chunk):
        if not self.enabled:
            return chunk
        self.buffer += chunk
        cut = self.safe_cut(self.buffer)
        ready, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return speak_polish(ready, self.said_contacts) if ready else ""

    def flush(self):
        if not self.enabled:
            return ""
        ready, self.buffer = self.buffer, ""
        return speak_polish(ready, self.said_contacts) if ready else ""

    def safe_cut(self, text):
        """Index up to which text is final: the last space before a word that is still incomplete or could join a
        number, address or range with what comes next."""
        tokens = list(re.finditer(r"\S+", text))
        if not tokens:
            return 0
        # The last token may still be growing (no space after it yet), so it's always held.
        index = len(tokens) - 1 if not text[-1].isspace() else len(tokens)
        while index > 0 and self.must_wait(tokens[index - 1].group(0)):
            index -= 1
        if index == len(tokens):
            return len(text)
        return tokens[index].start()

    def must_wait(self, token):
        bare = token.strip(".,;:!?()\"'„”")
        if not bare:
            return False
        if token.endswith((".", "!", "?")) and not re.search(r"[\d@/]|\.\w", bare) and bare.lower() not in ("ok", "sp"):
            return False
        return bool(self.HOLD_BACK.fullmatch(bare)) or bool(re.fullmatch(r"[-–—]", bare))

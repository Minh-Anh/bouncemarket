"""Decides whether a username a player typed can go on the public leaderboard.

Everyone who opens the page sees the leaderboard, so a name has to pass three checks:
1. It uses only plain letters, numbers, - and _, and is 1 to 16 characters long.
2. It isn't a reserved name, like "you", that would look like the site itself talking.
3. It doesn't contain a slur, obscenity or hate symbol, even a disguised one.

The third check is a word filter, and word filters have a famous problem: "Scunthorpe" is a
town in England with an obscene word inside it. So short words that hide inside ordinary ones
(ass in "class", rape in "grape") are only blocked when they stand on their own, and long,
unmistakable words are blocked wherever they appear. When in doubt we let a name through: an
ordinary name being refused is worse than a rare clever one slipping past, and anything that
does slip past can be removed by hand.

Everything here is worked out once, when the server starts, so checking a name is quick.
"""

import itertools
import re

BAD_CHARACTERS = "usernames are 1 to 16 letters, numbers, - or _."
NOT_ALLOWED = "sorry, that username isn't allowed."

# Only these characters, and at least one of them a letter or number, so "-" or "__" aren't names.
# Keeping to plain ASCII also rules out lookalike letters from other alphabets, invisible
# characters, and very wide symbols that would break the layout of the leaderboard.
ALLOWED = re.compile(r"[A-Za-z0-9_-]{1,16}")
HAS_LETTER_OR_DIGIT = re.compile(r"[A-Za-z0-9]")

# Names that would look like the site speaking, or like part of the page. The page shows a
# player without a name as "you" on the leaderboard, so a player really called "you" would
# confuse everyone. These are compared ignoring capitals, - and _, and with numbers read as
# letters, so "Y0U" and "ad_m1n" are caught too.
RESERVED = {
    "you", "guest",
    "admin", "administrator", "sysadmin", "root", "mod", "moderator", "staff", "official",
    "owner", "support", "system", "server", "bouncemarket", "counterparty",
    "null", "undefined",
}

# People disguise letters as numbers that look like them, e.g. "sh1t" or "a55". 1 can stand
# for i or l, so reserved names are checked both ways.
NUMBERS_AS_I = str.maketrans("0134578", "oieastb")
NUMBERS_AS_L = str.maketrans("0134578", "oleastb")

# Inside the word filter, each letter also matches the numbers and letters that are used to
# disguise it. u matches v because of spellings like "fvck".
LOOKALIKES = {
    "a": "a4", "b": "b8", "e": "e3", "g": "g69", "i": "i1", "l": "l1",
    "o": "o0", "s": "s5", "t": "t7", "u": "uv", "z": "z2",
}


def words(text):
    """The words in a block of the list below, ignoring the # comments."""
    return [word for line in text.splitlines() for word in line.split("#")[0].split()]


# Words that are offensive wherever they appear in a name, even run together with other words,
# as in "xXfuckXx". Each one is long or unusual enough that it doesn't turn up inside ordinary
# words, apart from the few listed in INNOCENT below.
ANYWHERE = words("""
    # Racial and ethnic slurs
    nigger nigga niggah nigguh niglet nignog sandnigger
    wetback raghead towelhead jigaboo porchmonkey junglebunny zipperhead chingchong
    spearchucker cameljockey pickaninny slanteye kaffir golliwog redskin

    # Homophobic and transphobic slurs
    faggot faggit fagget tranny trannie shemale fudgepacker carpetmuncher battyboy poofter

    # Ableist slurs
    retard mongoloid

    # Strong profanity, including the usual misspellings
    fuck fck phuck cunt bitch wanker asshole arsehole bollocks bellend douchebag
    motherf cocksuck dicksuck dickhead dickface shithead shitface shithole shitlord shitpost
    bullshit dipshit

    # Explicit sexual terms
    porn penis vagina clitoris masturbat orgasm dildo jizz blowjob handjob rimjob buttplug
    gangbang bukkake fellatio cunnilingus hentai cumshot cumslut cumdump analsex buttsex
    titties slut whore skank smegma felch queef nutsack ballsack incest bestiality
    zoophil necrophil

    # Sexual violence and abuse of children
    molest pedophil paedophil pedofil lolicon

    # Nazi and white-supremacist references
    hitler nazi swastika siegheil seigheil kkk kukluxklan whitepower whitepride
    gasthejews holohoax zyklon
""")

# Ordinary words that happen to contain one of the words above. They're taken out of a name
# before it's checked, so "Scunthorpe" and "snigger" are fine but "scunthorpe_cunt" is not.
INNOCENT = words("""
    scunthorpe   # an English town, the classic example
    snigger      # to laugh unkindly
    niggard      # a miser, and "niggardly"
    ashkenaz     # Ashkenazi, Jews of central and eastern European descent
    nazia nazih nazim nazir nazif   # Arabic and Urdu first names, like Nazir and Nazia
    retarda retardi retarder pretard   # fire retardant, retardation, retarding, pretardy
    penistone pennis   # an English town, and words like Pennisetum (penis with a doubled n)
    poorn        # poorness (porn with a doubled o)
    whittl       # whittle, Whittler (hitler with a doubled t)
    swank        # swanky, swanker
    slutsk       # Slutsk, a town in Belarus, and the surname Slutsky
""")

# Words that are only offensive as a whole word, because they also hide inside ordinary ones:
# ass in "class", cock in "peacock", rape in "grape", spic in "spice". They're blocked only when
# they're the whole name, or a whole part of it between - or _ or numbers, like "big_cock" or
# "ass99". Some are real words or names with an innocent meaning too (a chink in armour, a
# dyke on a canal, Dick as a first name), but as a username the offensive meaning wins.
WHOLE_WORD = words("""
    # Racial and ethnic slurs
    nig nigs kike kikes spic spics spick chink chinks gook gooks coon coons
    wop wops jap japs paki pakis darkie darky beaner beaners yid yids heeb squaw injun

    # Homophobic slurs
    fag fags faggy dyke dykes

    # Ableist slurs
    spaz spastic

    # Profanity
    fuk fukk fuq   # not anywhere in a name, because of Japanese names like Fukuda and Fukushima
    ass asses arse shit shits shite shitty shitter twat twats wank wanks bastard bastards

    # Sexual terms
    cock cocks dick dicks prick pricks pussy pussies tit tits titty boob boobs cum cums clit anal anus sex milf milfs

    # Sexual violence and abuse of children
    rape rapes raped raper rapist rapists raping rapey pedo pedos paedo paedos
""")


def pattern(word, doubles_allowed):
    """A regular expression for one word of the list, which also matches it disguised.

    Each letter can be any of its lookalikes, as in "sh1t", and can be repeated, as in "fuuuck".
    A letter that's doubled in the word must be doubled in the name too, so "nigger" doesn't
    also catch "Niger", the country.

    doubles_allowed says whether a single letter in the word also matches a doubled one in the
    name. For the long words it does, so "fucck" is caught. For the short words it doesn't,
    because ordinary words are only a doubled letter away from them: "rapper" from "raper",
    "assess" from "asses". A short word still matches with a letter written three or more
    times, as in "assss", which nobody types by accident.
    """
    parts = []
    for letter, run in itertools.groupby(word):
        count = len(list(run))
        letters = "[" + LOOKALIKES.get(letter, letter) + "]"
        if count > 1:
            parts.append(letters + "{%d,}" % count)
        elif doubles_allowed:
            parts.append(letters + "+")
        else:
            parts.append(letters + "(?:" + letters + "{2,})?")
    return "".join(parts)


ANYWHERE_RE = re.compile("|".join(pattern(word, doubles_allowed=True) for word in ANYWHERE))
WHOLE_WORD_RE = re.compile("|".join(pattern(word, doubles_allowed=False) for word in WHOLE_WORD))
INNOCENT_RE = re.compile("|".join(INNOCENT))
SEPARATORS = re.compile(r"[_-]+")
LETTERS = re.compile(r"[a-z]+")


def name_problem(name: str) -> str | None:
    """None if this username is acceptable. Otherwise a short message to show the player."""
    # Check the length first, so a very long name costs nothing more to turn down.
    if not 1 <= len(name) <= 16 or not ALLOWED.fullmatch(name) or not HAS_LETTER_OR_DIGIT.search(name):
        return BAD_CHARACTERS

    lower = name.lower()
    parts = [part for part in SEPARATORS.split(lower) if part]  # "Big_C0ck" -> ["big", "c0ck"]
    joined = "".join(parts)  # "f_u_c_k" -> "fuck"

    if joined.translate(NUMBERS_AS_I) in RESERVED or joined.translate(NUMBERS_AS_L) in RESERVED:
        return NOT_ALLOWED

    # 14 and 88 together are a well-known neo-Nazi code. Either number alone is fine.
    if "1488" in joined:
        return NOT_ALLOWED

    # Look for the long words anywhere, once each part has had its innocent words taken out.
    # The innocent words are taken out part by part, so splitting a slur with a separator, as
    # in "s_nigger", doesn't make it look like the innocent "snigger".
    if ANYWHERE_RE.search("".join(INNOCENT_RE.sub(".", part) for part in parts)):
        return NOT_ALLOWED

    # Look for the short words as a whole: the whole name ("a-s-s"), each part between - or _
    # ("a55"), and each run of letters between numbers ("ass99"). A part has to contain a letter
    # to be read this way, so numbers like 455 or 8008 stay numbers.
    candidates = {joined, *parts, *LETTERS.findall(lower)}
    for candidate in candidates:
        if LETTERS.search(candidate) and WHOLE_WORD_RE.fullmatch(candidate):
            return NOT_ALLOWED

    return None

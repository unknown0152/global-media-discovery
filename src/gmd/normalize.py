"""Normalization helpers shared by collectors and the resolver."""

from __future__ import annotations

from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
import re
import unicodedata
from typing import Any, Iterable

_SPACE_RE = re.compile(r"\s+")
_YEAR_SUFFIX_RE = re.compile(r"\s*[\(\[](?:19|20)\d{2}[\)\]]\s*$")
_NON_WORD_RE = re.compile(r"[^\w]+", flags=re.UNICODE)

COUNTRY_ALPHA3_TO_ALPHA2: dict[str, str] = {
    'abw': 'AW',
    'afg': 'AF',
    'ago': 'AO',
    'aia': 'AI',
    'ala': 'AX',
    'alb': 'AL',
    'and': 'AD',
    'are': 'AE',
    'arg': 'AR',
    'arm': 'AM',
    'asm': 'AS',
    'ata': 'AQ',
    'atf': 'TF',
    'atg': 'AG',
    'aus': 'AU',
    'aut': 'AT',
    'aze': 'AZ',
    'bdi': 'BI',
    'bel': 'BE',
    'ben': 'BJ',
    'bes': 'BQ',
    'bfa': 'BF',
    'bgd': 'BD',
    'bgr': 'BG',
    'bhr': 'BH',
    'bhs': 'BS',
    'bih': 'BA',
    'blm': 'BL',
    'blr': 'BY',
    'blz': 'BZ',
    'bmu': 'BM',
    'bol': 'BO',
    'bra': 'BR',
    'brb': 'BB',
    'brn': 'BN',
    'btn': 'BT',
    'bvt': 'BV',
    'bwa': 'BW',
    'caf': 'CF',
    'can': 'CA',
    'cck': 'CC',
    'che': 'CH',
    'chl': 'CL',
    'chn': 'CN',
    'civ': 'CI',
    'cmr': 'CM',
    'cod': 'CD',
    'cog': 'CG',
    'cok': 'CK',
    'col': 'CO',
    'com': 'KM',
    'cpv': 'CV',
    'cri': 'CR',
    'cub': 'CU',
    'cuw': 'CW',
    'cxr': 'CX',
    'cym': 'KY',
    'cyp': 'CY',
    'cze': 'CZ',
    'deu': 'DE',
    'dji': 'DJ',
    'dma': 'DM',
    'dnk': 'DK',
    'dom': 'DO',
    'dza': 'DZ',
    'ecu': 'EC',
    'egy': 'EG',
    'eri': 'ER',
    'esh': 'EH',
    'esp': 'ES',
    'est': 'EE',
    'eth': 'ET',
    'fin': 'FI',
    'fji': 'FJ',
    'flk': 'FK',
    'fra': 'FR',
    'fro': 'FO',
    'fsm': 'FM',
    'gab': 'GA',
    'gbr': 'GB',
    'geo': 'GE',
    'ggy': 'GG',
    'gha': 'GH',
    'gib': 'GI',
    'gin': 'GN',
    'glp': 'GP',
    'gmb': 'GM',
    'gnb': 'GW',
    'gnq': 'GQ',
    'grc': 'GR',
    'grd': 'GD',
    'grl': 'GL',
    'gtm': 'GT',
    'guf': 'GF',
    'gum': 'GU',
    'guy': 'GY',
    'hkg': 'HK',
    'hmd': 'HM',
    'hnd': 'HN',
    'hrv': 'HR',
    'hti': 'HT',
    'hun': 'HU',
    'idn': 'ID',
    'imn': 'IM',
    'ind': 'IN',
    'iot': 'IO',
    'irl': 'IE',
    'irn': 'IR',
    'irq': 'IQ',
    'isl': 'IS',
    'isr': 'IL',
    'ita': 'IT',
    'jam': 'JM',
    'jey': 'JE',
    'jor': 'JO',
    'jpn': 'JP',
    'kaz': 'KZ',
    'ken': 'KE',
    'kgz': 'KG',
    'khm': 'KH',
    'kir': 'KI',
    'kna': 'KN',
    'kor': 'KR',
    'kwt': 'KW',
    'lao': 'LA',
    'lbn': 'LB',
    'lbr': 'LR',
    'lby': 'LY',
    'lca': 'LC',
    'lie': 'LI',
    'lka': 'LK',
    'lso': 'LS',
    'ltu': 'LT',
    'lux': 'LU',
    'lva': 'LV',
    'mac': 'MO',
    'maf': 'MF',
    'mar': 'MA',
    'mco': 'MC',
    'mda': 'MD',
    'mdg': 'MG',
    'mdv': 'MV',
    'mex': 'MX',
    'mhl': 'MH',
    'mkd': 'MK',
    'mli': 'ML',
    'mlt': 'MT',
    'mmr': 'MM',
    'mne': 'ME',
    'mng': 'MN',
    'mnp': 'MP',
    'moz': 'MZ',
    'mrt': 'MR',
    'msr': 'MS',
    'mtq': 'MQ',
    'mus': 'MU',
    'mwi': 'MW',
    'mys': 'MY',
    'myt': 'YT',
    'nam': 'NA',
    'ncl': 'NC',
    'ner': 'NE',
    'nfk': 'NF',
    'nga': 'NG',
    'nic': 'NI',
    'niu': 'NU',
    'nld': 'NL',
    'nor': 'NO',
    'npl': 'NP',
    'nru': 'NR',
    'nzl': 'NZ',
    'omn': 'OM',
    'pak': 'PK',
    'pan': 'PA',
    'pcn': 'PN',
    'per': 'PE',
    'phl': 'PH',
    'plw': 'PW',
    'png': 'PG',
    'pol': 'PL',
    'pri': 'PR',
    'prk': 'KP',
    'prt': 'PT',
    'pry': 'PY',
    'pse': 'PS',
    'pyf': 'PF',
    'qat': 'QA',
    'reu': 'RE',
    'rou': 'RO',
    'rus': 'RU',
    'rwa': 'RW',
    'sau': 'SA',
    'sdn': 'SD',
    'sen': 'SN',
    'sgp': 'SG',
    'sgs': 'GS',
    'shn': 'SH',
    'sjm': 'SJ',
    'slb': 'SB',
    'sle': 'SL',
    'slv': 'SV',
    'smr': 'SM',
    'som': 'SO',
    'spm': 'PM',
    'srb': 'RS',
    'ssd': 'SS',
    'stp': 'ST',
    'sur': 'SR',
    'svk': 'SK',
    'svn': 'SI',
    'swe': 'SE',
    'swz': 'SZ',
    'sxm': 'SX',
    'syc': 'SC',
    'syr': 'SY',
    'tca': 'TC',
    'tcd': 'TD',
    'tgo': 'TG',
    'tha': 'TH',
    'tjk': 'TJ',
    'tkl': 'TK',
    'tkm': 'TM',
    'tls': 'TL',
    'ton': 'TO',
    'tto': 'TT',
    'tun': 'TN',
    'tur': 'TR',
    'tuv': 'TV',
    'twn': 'TW',
    'tza': 'TZ',
    'uga': 'UG',
    'ukr': 'UA',
    'umi': 'UM',
    'ury': 'UY',
    'usa': 'US',
    'uzb': 'UZ',
    'vat': 'VA',
    'vct': 'VC',
    'ven': 'VE',
    'vgb': 'VG',
    'vir': 'VI',
    'vnm': 'VN',
    'vut': 'VU',
    'wlf': 'WF',
    'wsm': 'WS',
    'xkx': 'XK',
    'yem': 'YE',
    'zaf': 'ZA',
    'zmb': 'ZM',
    'zwe': 'ZW',
}

LANG_ALPHA3_TO_ALPHA2: dict[str, str] = {
    'aar': 'aa',
    'abk': 'ab',
    'afr': 'af',
    'aka': 'ak',
    'alb': 'sq',
    'amh': 'am',
    'ara': 'ar',
    'arg': 'an',
    'arm': 'hy',
    'asm': 'as',
    'ava': 'av',
    'ave': 'ae',
    'aym': 'ay',
    'aze': 'az',
    'bak': 'ba',
    'bam': 'bm',
    'baq': 'eu',
    'bel': 'be',
    'ben': 'bn',
    'bis': 'bi',
    'bod': 'bo',
    'bos': 'bs',
    'bre': 'br',
    'bul': 'bg',
    'bur': 'my',
    'cat': 'ca',
    'ces': 'cs',
    'cha': 'ch',
    'che': 'ce',
    'chi': 'zh',
    'chu': 'cu',
    'chv': 'cv',
    'cor': 'kw',
    'cos': 'co',
    'cre': 'cr',
    'cym': 'cy',
    'cze': 'cs',
    'dan': 'da',
    'deu': 'de',
    'div': 'dv',
    'dut': 'nl',
    'dzo': 'dz',
    'ell': 'el',
    'eng': 'en',
    'epo': 'eo',
    'est': 'et',
    'eus': 'eu',
    'ewe': 'ee',
    'fao': 'fo',
    'fas': 'fa',
    'fij': 'fj',
    'fin': 'fi',
    'fra': 'fr',
    'fre': 'fr',
    'fry': 'fy',
    'ful': 'ff',
    'geo': 'ka',
    'ger': 'de',
    'gla': 'gd',
    'gle': 'ga',
    'glg': 'gl',
    'glv': 'gv',
    'gre': 'el',
    'grn': 'gn',
    'guj': 'gu',
    'hat': 'ht',
    'hau': 'ha',
    'hbs': 'sh',
    'heb': 'he',
    'her': 'hz',
    'hin': 'hi',
    'hmo': 'ho',
    'hrv': 'hr',
    'hun': 'hu',
    'hye': 'hy',
    'ibo': 'ig',
    'ice': 'is',
    'ido': 'io',
    'iii': 'ii',
    'iku': 'iu',
    'ile': 'ie',
    'ina': 'ia',
    'ind': 'id',
    'ipk': 'ik',
    'isl': 'is',
    'ita': 'it',
    'jav': 'jv',
    'jpn': 'ja',
    'kal': 'kl',
    'kan': 'kn',
    'kas': 'ks',
    'kat': 'ka',
    'kau': 'kr',
    'kaz': 'kk',
    'khm': 'km',
    'kik': 'ki',
    'kin': 'rw',
    'kir': 'ky',
    'kom': 'kv',
    'kon': 'kg',
    'kor': 'ko',
    'kua': 'kj',
    'kur': 'ku',
    'lao': 'lo',
    'lat': 'la',
    'lav': 'lv',
    'lim': 'li',
    'lin': 'ln',
    'lit': 'lt',
    'ltz': 'lb',
    'lub': 'lu',
    'lug': 'lg',
    'mac': 'mk',
    'mah': 'mh',
    'mal': 'ml',
    'mao': 'mi',
    'mar': 'mr',
    'may': 'ms',
    'mkd': 'mk',
    'mlg': 'mg',
    'mlt': 'mt',
    'mon': 'mn',
    'mri': 'mi',
    'msa': 'ms',
    'mya': 'my',
    'nau': 'na',
    'nav': 'nv',
    'nbl': 'nr',
    'nde': 'nd',
    'ndo': 'ng',
    'nep': 'ne',
    'nld': 'nl',
    'nno': 'nn',
    'nob': 'nb',
    'nor': 'no',
    'nya': 'ny',
    'oci': 'oc',
    'oji': 'oj',
    'ori': 'or',
    'orm': 'om',
    'oss': 'os',
    'pan': 'pa',
    'per': 'fa',
    'pli': 'pi',
    'pol': 'pl',
    'por': 'pt',
    'pt': 'pt',
    'pus': 'ps',
    'que': 'qu',
    'roh': 'rm',
    'ron': 'ro',
    'rum': 'ro',
    'run': 'rn',
    'rus': 'ru',
    'sag': 'sg',
    'san': 'sa',
    'sin': 'si',
    'slk': 'sk',
    'slo': 'sk',
    'slv': 'sl',
    'sme': 'se',
    'smo': 'sm',
    'sna': 'sn',
    'snd': 'sd',
    'som': 'so',
    'sot': 'st',
    'spa': 'es',
    'sqi': 'sq',
    'srd': 'sc',
    'srp': 'sr',
    'ssw': 'ss',
    'sun': 'su',
    'swa': 'sw',
    'swe': 'sv',
    'tah': 'ty',
    'tam': 'ta',
    'tat': 'tt',
    'tel': 'te',
    'tgk': 'tg',
    'tgl': 'tl',
    'tha': 'th',
    'tib': 'bo',
    'tir': 'ti',
    'ton': 'to',
    'tsn': 'tn',
    'tso': 'ts',
    'tuk': 'tk',
    'tur': 'tr',
    'twi': 'tw',
    'uig': 'ug',
    'ukr': 'uk',
    'urd': 'ur',
    'uzb': 'uz',
    'ven': 've',
    'vie': 'vi',
    'vol': 'vo',
    'wel': 'cy',
    'wln': 'wa',
    'wol': 'wo',
    'xho': 'xh',
    'yid': 'yi',
    'yor': 'yo',
    'zha': 'za',
    'zho': 'zh',
    'zhtw': 'zh-Hant',
    'zul': 'zu',
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = " ".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value)
    return clean_text(unescape(text))


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def normalize_title(value: str | None) -> str:
    text = clean_text(value).casefold()
    text = _YEAR_SUFFIX_RE.sub("", text)
    text = text.replace("&", " and ")
    text = _NON_WORD_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    code = value.strip()
    if len(code) == 2:
        return code.upper()
    return COUNTRY_ALPHA3_TO_ALPHA2.get(code.lower(), code.upper())


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    code = value.strip()
    if len(code) == 2:
        return code.lower()
    return LANG_ALPHA3_TO_ALPHA2.get(code.lower(), code.lower())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{sha256(material.encode('utf-8')).hexdigest()[:24]}"


def unique_clean(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def infer_format(
    *,
    explicit: str | None = None,
    genres: Iterable[str] = (),
    tags: Iterable[str] = (),
) -> str:
    explicit_clean = clean_text(explicit)
    genre_values = {clean_text(item).casefold() for item in genres}
    tag_values = {clean_text(item).casefold() for item in tags}

    if "animation" in genre_values or explicit_clean.casefold() == "animation":
        return "Animation"
    if "documentary" in genre_values or "docuseries" in tag_values:
        return "Documentary"
    if explicit_clean.casefold() in {"reality", "game show", "talk show", "news"}:
        return explicit_clean.title()
    if "reality" in genre_values or any("reality" in item for item in tag_values):
        return "Reality"
    if explicit_clean:
        return explicit_clean
    return "Scripted"

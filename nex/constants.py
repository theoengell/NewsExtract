"""Shared path defaults and static configuration blobs."""

DEFAULT_CACHE_FILE = "config/headlines_cache.json"
DEFAULT_CATEGORIES_FILE = "config/categories.json"
LEGACY_COLOR_MAP_FILE = "config/url_colors.json"
DEFAULT_SITES_FILE = "config/sites.json"
DEFAULT_SETTINGS_FILE = "config/settings.json"
DEFAULT_CLUSTER_IGNORE_FILE = "config/cluster_ignore_words.json"
DEFAULT_HTML_FILE = "headlines.html"
DEFAULT_LOGOS_DIR = "logos"
APP_VERSION = "0.2.0"
VERSION_MANIFEST_URL = "https://raw.githubusercontent.com/theoengell/NewsExtract/main/version.json"
VERSION_CHECK_TIMEOUT = 2.5
APP_REPO_URL = "https://github.com/theoengell/NewsExtract"

def make_default_settings():
    return {
        "show_external": True,
        "only_new": False,
        "bg_strength": 35,
        "seen_limit": 15,
        "highlight_words": "",
        "exclude_words": "",
        "site_order": [],
        "show_all_new": True,
        "dim_opened": True,
        "show_opened_today": True,
        "dark_mode": False,
        "show_clusters": False,
        "cluster_view": "list",
        "cluster_min_size": 2,
        "languages": {"da": True, "en": True},
    }

DEFAULT_CATEGORIES = {
    "nyheder": {
        "label": "Nyheder",
        "color": "#e3f2fd",
        "match": ["/nyheder/", "/seneste/"],
        "enabled": True,
    },
    "indland": {
        "label": "Indland",
        "color": "#80cbc4",
        "match": ["/indland/", "/danmark/", "/samfund/"],
        "enabled": True,
    },
    "udland": {
        "label": "Udland",
        "color": "#ce93d8",
        "match": ["/udland/", "/internationalt/"],
        "enabled": True,
    },
    "politik": {
        "label": "Politik",
        "color": "#90caf9",
        "match": ["/politik/", "/danskpolitik/"],
        "enabled": True,
    },
    "krimi": {
        "label": "Krimi",
        "color": "#ef9a9a",
        "match": ["/krimi/"],
        "enabled": True,
    },
    "sport": {
        "label": "Sport",
        "color": "#a5d6a7",
        "match": ["/sport/", "/sporten/", "/fodbold/", "/cykling/", "/anden_sport/", "/superligaen/"],
        "enabled": True,
    },
    "penge": {
        "label": "Penge",
        "color": "#fff59d",
        "match": ["/penge/", "/virksomheder/", "/okonomi/", "/økonomi/"],
        "enabled": True,
    },
    "forbrug": {
        "label": "Forbrug",
        "color": "#ffcc80",
        "match": ["/forbrug/", "/sundhed/", "/vores-liv/"],
        "enabled": True,
    },
    "kultur": {
        "label": "Kultur",
        "color": "#b39ddb",
        "match": ["/kultur/", "/musik/", "/filmogtv/", "/film_og_tv/"],
        "enabled": True,
    },
    "underholdning": {
        "label": "Underholdning",
        "color": "#f8bbd0",
        "match": ["/underholdning/", "/royale/", "/dkkendte/", "/udlandkendte/", "/int-kendte/", "/side9/"],
        "enabled": True,
    },
    "debat": {
        "label": "Debat",
        "color": "#bcaaa4",
        "match": ["/debat/", "/debatindlaeg/", "/ledere/", "/kommentatorer/"],
        "enabled": True,
    },
    "live": {
        "label": "Live",
        "color": "#ffab91",
        "match": ["/live/"],
        "enabled": True,
    },
    "vejret": {
        "label": "Vejr & trafik",
        "color": "#b2ebf2",
        "match": ["/vejret/", "/trafik/"],
        "enabled": True,
    },
    "other": {
        "label": "Other",
        "color": "#eeeeee",
        "match": [],
        "enabled": True,
    },
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "da,sv,nb,no,en-US;q=0.9,en;q=0.8",
}

KNOWN_LANGUAGES = {
    "da": "Danish",
    "en": "English",
    "sv": "Swedish",
    "no": "Norwegian",
}

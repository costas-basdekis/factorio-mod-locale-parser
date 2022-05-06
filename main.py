#!/usr/bin/env python3
import json
import re
import tempfile
from json import JSONDecodeError
from pathlib import Path
import zipfile
import configparser
from typing import Dict, Optional, Tuple, Iterator, Iterable
from tqdm import tqdm
import requests

API_AUTH_FILE = Path("./api_auth.json")

FACTORIO_ROOT = Path.home() / ".factorio"
FACTORIO_MOD_ROOT = FACTORIO_ROOT / "mods"
FACTORIO_CORE_LOCALE_ROOT = (
    Path.home() / ".steam" / "steam" / "steamapps" / "common" / "Factorio"
    / "data" / "core" / "locale"
)

MOD_SETTINGS_DATA_PATH = Path("./mod_settings_data.json")
MOD_SETTINGS_DATA_TEMPORARY_PATH = Path("./mod_settings_data.temporary.json")
CORE_SETTINGS_DATA_PATH = Path("./core_settings_data.json")

RE_INFO = re.compile(r"[^/]+/info.json", re.IGNORECASE)
RE_MOD_LOCALE_PATH = re.compile(r"[^/]+/locale/([\w-]+)/[^/]+.cfg")
RE_CORE_LOCALE_PATH = re.compile(r".*/locale/([\w-]+)/[^/]+.cfg")


def main():
    by_locale = update_game_locale_data()
    update_mod_locale_data(by_locale)


def update_game_locale_data() -> Dict:
    locale_paths = [
        path
        for path in FACTORIO_CORE_LOCALE_ROOT.glob("**/*")
        if RE_CORE_LOCALE_PATH.match(str(path))
    ]
    by_locale: Dict[str, Dict[str, str]] = {}
    for locale_path in locale_paths:
        locale_name, = RE_CORE_LOCALE_PATH.match(str(locale_path)).groups()
        try:
            config_text = locale_path.read_text()
        except UnicodeDecodeError:
            print(
                f"Could not parse core {locale_name} locale file "
                f"{locale_path}")
            continue
        config = configparser.RawConfigParser(strict=False)
        try:
            config.read_string(config_text)
        except configparser.MissingSectionHeaderError:
            config.read_string("[DEFAULT]\r\n" + config_text)
        except configparser.ParsingError:
            print(f"Could not read core config file {locale_path}")
            continue
        for_locale = by_locale.setdefault(locale_name, {})
        if "gui-about" in config:
            version = dict(config.items("gui-about")).get("version")
            if version:
                for_locale["version"] = version
        if "gui-mod-settings" in config:
            gui_mod_settings = dict(config.items("gui-mod-settings"))
            for_locale.update({
                mapped_key: gui_mod_settings[key]
                for key, mapped_key in [
                    ("title", "title"),
                    ("startup", "startup"),
                    ("map", "runtime-global"),
                    ("per-player", "runtime-per-user"),
                ]
                if gui_mod_settings.get(key)
            })

    with open(CORE_SETTINGS_DATA_PATH, "w") as f:
        json.dump({
            "core": by_locale,
        }, f, indent=2)

    return by_locale


def update_mod_locale_data(by_locale: Dict):
    mod_data, locale_data = get_mods_settings_locale_data(
        FACTORIO_MOD_ROOT, False)

    print(len(mod_data), "mods")
    print(len(locale_data), "settings")
    save_mod_settings_data(mod_data, locale_data, by_locale=by_locale)


def save_mod_settings_data(
        mod_data: Dict, locale_data: Dict, temporary: bool = False,
        by_locale: Optional[Dict] = None) -> None:
    locales = sorted({
        locale
        for setting_data in locale_data.values()
        for by_mod_and_language in setting_data["by_mod_and_language"].values()
        for locale in by_mod_and_language
    } | set(by_locale or {}))
    if temporary:
        path = MOD_SETTINGS_DATA_TEMPORARY_PATH
    else:
        path = MOD_SETTINGS_DATA_PATH
        mod_data = {
            name: mod
            for name, mod in mod_data.items()
            if mod["setting_names"]
        }
    with open(path, "w") as f:
        json.dump({
            "core": by_locale,
            "locales": locales,
            "mods": mod_data,
            "settings": locale_data,
        }, f, indent=2)


def get_mods_settings_locale_data(
    factorio_mod_root: Path, local: bool,
) -> Tuple[Dict, Dict]:
    if MOD_SETTINGS_DATA_TEMPORARY_PATH.exists():
        with open(MOD_SETTINGS_DATA_TEMPORARY_PATH, "r") as f:
            all_data = json.load(f)
            mod_data = all_data["mods"]
            locale_data = all_data["settings"]
    else:
        mod_data = {}
        locale_data = {}

    if local:
        zip_files = iterate_local_mod_zip_files(factorio_mod_root)
    else:
        zip_files = iterate_zip_files_from_api(excluding_mods=mod_data)

    for index, (mod_api_data, zip_file) in enumerate(zip_files):
        if index % 10 == 0:
            print(f"Opened {index} zip files")
        # It was skipped
        if not zip_file:
            continue
        mod_info = get_mod_info(zip_file)
        if not mod_info:
            print(
                f"Could not find info file, name, or title, in mod ZIP "
                f"{zip_file.filename}")
            continue
        mod_name, mod_title, version = mod_info
        mod_data[mod_name] = {
            "name": mod_name,
            "title": mod_title,
            "version": version,
            "setting_names": [],
        }
        get_mod_settings_locale_data(zip_file, mod_name, locale_data, mod_data)
        if index % 10 == 0:
            save_mod_settings_data(mod_data, locale_data, temporary=True)

    # if MOD_SETTINGS_DATA_TEMPORARY_PATH.exists():
    #     MOD_SETTINGS_DATA_TEMPORARY_PATH.unlink()

    return mod_data, locale_data


def iterate_local_mod_zip_files(
        factorio_mod_root: Path) -> Iterator[Tuple[None, zipfile.ZipFile]]:
    mod_zips = [
        item
        for item in factorio_mod_root.glob("*_*.zip")
        if item.is_file()
    ]
    print(f"Found {len(mod_zips)} mod zips locally")
    for mod_zip in mod_zips:
        zip_file = zipfile.ZipFile(mod_zip)
        yield None, zip_file


MOD_URL_ROOT = "https://mods.factorio.com"


def iterate_zip_files_from_api(
    excluding_mods: Dict,
) -> Iterator[Tuple[Dict, Optional[zipfile.ZipFile]]]:
    try:
        with API_AUTH_FILE.open("rb") as f:
            api_auth = json.load(f)
    except FileNotFoundError:
        raise Exception(
            f"Missing API auth file at {API_AUTH_FILE} - copy "
            f"`player-data.json` to that path, copy the service username and "
            f"token to that file from `player-data.json`, or set the path at "
            f"`API_AUTH_FILE`")
    bad_zip_file_consecutive_count = 0
    for mod_api_data, should_skip in iterate_mods_from_api(excluding_mods=excluding_mods):
        if should_skip:
            # It was skipped
            yield mod_api_data, None
            continue
        download_url = (mod_api_data.get('latest_release', {}) or {})\
            .get('download_url')
        if not download_url:
            continue
        zip_url = (
            f"{MOD_URL_ROOT}"
            f"{download_url}"
            f"?username={api_auth['service-username']}"
            f"&token={api_auth['service-token']}"
        )
        response = requests.get(zip_url, stream=True)
        with tempfile.NamedTemporaryFile() as tmp:
            progress_bar = tqdm(
                desc=(
                    f"Downloading {mod_api_data['title']} "
                    f"({mod_api_data['name']})"
                ),
                total=int(response.headers.get('content-length', 0)),
                unit='iB', unit_scale=True)
            try:
                for data in response.iter_content(10 * 1024 * 1024):
                    progress_bar.update(len(data))
                    tmp.write(data)
            finally:
                progress_bar.close()
            tmp.flush()
            tmp.seek(0)
            try:
                zip_file = zipfile.ZipFile(tmp)
            except zipfile.BadZipfile:
                bad_zip_file_consecutive_count += 1
                if bad_zip_file_consecutive_count > 3:
                    raise Exception(
                        "File was not a ZIP - did you provide a valid username "
                        "and token?")
                print("File was not a ZIP")
                # Let upstream know you're skipping
                yield mod_api_data, None
                continue
            bad_zip_file_consecutive_count = 0
            yield mod_api_data, zip_file


MOD_API_URL_ROOT = "https://mods.factorio.com/api/mods"


def iterate_mods_from_api(excluding_mods: Dict) -> Iterator[Tuple[Dict, bool]]:
    next_url = (
        f"{MOD_API_URL_ROOT}"
        f"?hide_deprecated=true"
        f"&page_size=max"
    )
    while next_url:
        response = requests.get(next_url)
        data = response.json()
        for item in data["results"]:
            if item["name"] in excluding_mods and (not excluding_mods[item["name"]].get("version") or excluding_mods[item["name"]]["version"] >= item["latest_release"]["version"]):
                print(f"Skipping {item['name']}")
                # Let upstream know that we're skipping one, so that they can
                # keep track of the count so far
                yield item, True
                continue
            yield item, False
        next_url = ((data.get("pagination") or {}).get("links") or {}).get("next")


def get_mod_info(zip_file: zipfile.ZipFile) -> Optional[Tuple[str, str, str]]:
    all_paths = zip_file.namelist()
    info_filenames = [
        path
        for path in all_paths
        if RE_INFO.match(path)
    ]
    if not info_filenames:
        return None
    info_filename = info_filenames[0]
    try:
        with zip_file.open(info_filename) as f:
            info_data = json.load(f)
    except (UnicodeDecodeError, JSONDecodeError, RuntimeError):
        return None
    mod_name = info_data["name"]
    mod_title = info_data["title"]
    version = info_data["version"]

    return mod_name, mod_title, version


def get_mod_settings_locale_data(
    zip_file: zipfile.ZipFile, mod_name: str, locale_data: Dict,
    mod_data: Dict,
) -> None:
    locale_filenames = [
        path
        for path in zip_file.namelist()
        if RE_MOD_LOCALE_PATH.match(path)
    ]
    for locale_filename in locale_filenames:
        locale_name, = RE_MOD_LOCALE_PATH.match(locale_filename).groups()
        try:
            with zip_file.open(locale_filename) as f:
                config_source = f.read()
        except RuntimeError:
            print(
                f"Could not extract {locale_name} locale file "
                f"{locale_filename} of {mod_name}")
            continue
        try:
            config_text = config_source.decode()
        except UnicodeDecodeError:
            print(
                f"Could not parse {locale_name} locale file "
                f"{locale_filename} of {mod_name}")
            continue
        if "\ufeff" in config_text:
            config_text = config_source.decode("utf-8-sig")
        get_settings_locale_from_config(
            mod_name, locale_name, config_text, locale_data, mod_data)


RE_OLD_LOCALE_SECTION_FORMAT = re.compile(
    "(.*)_(?:map|pref|user)_settings", re.IGNORECASE)
RE_OLD_LOCALE_DESCRIPTION_FORMAT = re.compile("(.*)-desc", re.IGNORECASE)


def get_settings_locale_from_config(
    mod_name: str, locale_name: str, config_text: str, locale_data: Dict,
    mod_data: Dict,
) -> None:
    config = configparser.RawConfigParser(strict=False)
    try:
        config.read_string(config_text)
    except configparser.MissingSectionHeaderError:
        try:
            config.read_string("[DEFAULT]\r\n" + config_text)
        except configparser.ParsingError:
            print(f"Could not read config file for {mod_name}")
            return
    except configparser.ParsingError:
        print(f"Could not read config file for {mod_name}")
        return

    def add_setting_label(_setting_name, _setting_label):
        setting_data = locale_data.setdefault(_setting_name, {
            "name": _setting_name,
            "by_mod_and_language": {},
        })
        by_mod_and_language_data = setting_data["by_mod_and_language"] \
            .setdefault(mod_name, {}) \
            .setdefault(locale_name, {
                "mod": mod_name,
                "locale": locale_name,
                "label": _setting_label,
                "description": "",
            })
        by_mod_and_language_data["label"] = _setting_label
        if _setting_name not in mod_data[mod_name]["setting_names"]:
            mod_data[mod_name]["setting_names"].append(_setting_name)

    def add_setting_description(_setting_name, _setting_description):
        setting_data = locale_data.setdefault(_setting_name, {
            "name": _setting_name,
            "by_mod_and_language": {},
        })
        by_mod_and_language_data = setting_data["by_mod_and_language"] \
            .setdefault(mod_name, {}) \
            .setdefault(locale_name, {
                "mod": mod_name,
                "locale": locale_name,
                "label": "",
                "description": _setting_description,
            })
        by_mod_and_language_data["description"] = _setting_description
        if _setting_name not in mod_data[mod_name]["setting_names"]:
            mod_data[mod_name]["setting_names"].append(_setting_name)

    if "mod-setting-name" in config:
        for setting_name, setting_label in config.items("mod-setting-name"):
            add_setting_label(setting_name, setting_label)
    if "mod-setting-description" in config:
        for setting_name, setting_description \
                in config.items("mod-setting-description"):
            add_setting_description(setting_name, setting_description)
    for section in config:
        match = RE_OLD_LOCALE_SECTION_FORMAT.match(section)
        if not match:
            continue
        prefix, = match.groups()
        for key, value in config.items(section):
            description_match = RE_OLD_LOCALE_DESCRIPTION_FORMAT.match(key)
            if description_match:
                setting_suffix, = description_match.groups()
                setting_name = f"{prefix}_{setting_suffix}".replace("-", "_")
                setting_description = value
                add_setting_description(setting_name, setting_description)
            else:
                setting_suffix = key
                setting_name = f"{prefix}_{setting_suffix}".replace("-", "_")
                setting_label = value
                add_setting_label(setting_name, setting_label)


if __name__ == "__main__":
    main()

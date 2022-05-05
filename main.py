#!/usr/bin/env python3
import json
import re
from pathlib import Path
import zipfile
import configparser
from typing import Dict, Optional, Tuple

FACTORIO_ROOT = Path.home() / ".factorio"
FACTORIO_MOD_ROOT = FACTORIO_ROOT / "mods"


RE_INFO = re.compile(r"[^/]+/info.json", re.IGNORECASE)
RE_LOCALE_PATH = re.compile(r"[^/]+/locale/([\w-]+)/[^/]+.cfg")


def main():
    mod_data, locale_data = get_mods_settings_locale_data(FACTORIO_MOD_ROOT)
    locales = sorted({
        locale
        for setting_data in locale_data.values()
        for by_mod_and_language in setting_data["by_mod_and_language"].values()
        for locale in by_mod_and_language
    })

    print(len(mod_data), "mods")
    print(len(locale_data), "settings")
    with open("./mod_settings_data.json", "w") as f:
        json.dump({
            "locales": locales,
            "mods": mod_data,
            "settings": locale_data,
        }, f, indent=2)


def get_mods_settings_locale_data(factorio_mod_root: Path) -> Tuple[Dict, Dict]:
    mod_zips = [
        item
        for item in factorio_mod_root.glob("*_*.zip")
        if item.is_file()
    ]
    print(f"Found {len(mod_zips)} mod zips")
    mod_data = {}
    locale_data = {}
    for mod_zip in mod_zips:
        zip_file = zipfile.ZipFile(mod_zip)
        mod_info = get_mod_info(zip_file)
        if not mod_info:
            print(
                f"Could not find info file, name, or title, in mod ZIP "
                f"{mod_zip}")
            continue
        mod_name, mod_title = mod_info
        mod_data[mod_name] = {
            "name": mod_name,
            "title": mod_title,
            "setting_names": [],
        }
        get_mod_settings_locale_data(zip_file, mod_name, locale_data, mod_data)

    return mod_data, locale_data


def get_mod_info(zip_file: zipfile.ZipFile) -> Optional[Tuple[str, str]]:
    all_paths = zip_file.namelist()
    info_filenames = [
        path
        for path in all_paths
        if RE_INFO.match(path)
    ]
    if not info_filenames:
        return None
    info_filename = info_filenames[0]
    with zip_file.open(info_filename) as f:
        info_data = json.load(f)
    if not isinstance(info_data, dict) \
            or "name" not in info_data or "title" not in info_data:
        return None
    mod_name = info_data["name"]
    mod_title = info_data["title"]

    return mod_name, mod_title


def get_mod_settings_locale_data(
    zip_file: zipfile.ZipFile, mod_name: str, locale_data: Dict,
    mod_data: Dict,
) -> None:
    locale_filenames = [
        path
        for path in zip_file.namelist()
        if RE_LOCALE_PATH.match(path)
    ]
    for locale_filename in locale_filenames:
        locale_name, = RE_LOCALE_PATH.match(locale_filename).groups()
        with zip_file.open(locale_filename) as f:
            config_source = f.read()
            try:
                config_text = config_source.decode()
            except UnicodeDecodeError:
                print(
                    f"Could not parse {locale_name} locale file "
                    f"{locale_filename} or {mod_name}")
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
        config.read_string("[DEFAULT]\r\n" + config_text)

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

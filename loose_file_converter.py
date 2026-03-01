"""
Utilities for converting mod archive contents into LooseFileLoader payloads.

This module can:

1. Read a mod archive (.zip/.7z/.rar)
2. Resolve manifest/common/feature selections
3. Convert selected .fdata + .yumiamod.json pairs into loose files
4. Pass through already-loose asset files
5. Optionally write those loose files into a target mod subdirectory
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import struct
import zlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

import py7zr
import rarfile

from manifest_schema import MANIFEST_FILENAME, ModManifest, parse_manifest

SUPPORTED_EXTENSIONS = {".zip", ".7z", ".rar"}
LOOSE_ASSET_RE = re.compile(r"^(?:0x)?[0-9A-Fa-f]{8}\.[^.]+$")

# Taken from yumia_fdata_tools so the prototype can decode filename extensions
# without depending on a sibling checkout.
TKID_EXTENSIONS = {
    0x0BD05B27: "mit",
    0x0D34474D: "srst",
    0x17614AF5: "g1mx",
    0x1FDCAA40: "kidstask",
    0x20A6A0BB: "kidsobjdb",
    0x27BC54B7: "rigbin",
    0x4D0102AC: "g1em",
    0x4F16D0EF: "kts",
    0x5153729B: "mtl",
    0x54738C76: "g1co",
    0x5599AA51: "kscl",
    0x563BDEF1: "g1m",
    0x56D8DEDA: "sid",
    0x5C3E543C: "swg",
    0x6FA91671: "g1a",
    0x757347E0: "bpo",
    0x786DCD84: "g1n",
    0x79C724C2: "g1p",
    0x7BCD279F: "g1s",
    0x82945A44: "lsqtree",
    0x8E39AA37: "ktid",
    0x9CB3A4B6: "oidex",
    0xA027E46B: "mov",
    0xA8D88566: "g1cox",
    0xAD57EBBA: "g1t_new",
    0xAFBEC60C: "g1t",
    0xB097D41F: "g1e",
    0xB0A14534: "sgcbin",
    0xB1630F51: "kidsrender",
    0xB340861A: "mtl",
    0xBBD39F2D: "srsa",
    0xBBF9B49D: "grp",
    0xBE144B78: "ktid",
    0xBF6B52C7: "name",
    0xD7F47FB1: "efpl",
    0xDBCB74A9: "oid",
    0xE6A3C3BB: "oidex",
    0xED410290: "kts",
    0xF13845EF: "sclshape",
    0xF20DE437: "texinfo",
    0x1A6300FD: "g1es",
    0x1AB40AE8: "oid",
    0x2BCC0C02: "g1frani",
    0x32AC9403: "g1fpose",
    0x56EFE45C: "grp",
    0x5B2970FC: "ktf2",
    0x6DBD6EA6: "mit",
    0x133D2C3B: "sid",
}


@dataclass
class LooseFile:
    filename: str
    source_member: str
    data: bytes


@dataclass
class ConversionResult:
    archive_path: Path
    manifest: ModManifest | None
    mod_name: str
    selected_features: dict[str, str | None]
    files: list[LooseFile]


def _normalize_names(names: list[str]) -> list[str]:
    return [name.replace("\\", "/") for name in names]


def is_loose_asset_member(member: str) -> bool:
    return bool(LOOSE_ASSET_RE.match(Path(member).name))


def _list_archive_names(filepath: Path) -> list[str]:
    ext = filepath.suffix.lower()
    if ext == ".zip":
        with zipfile.ZipFile(filepath, "r") as zf:
            return _normalize_names(zf.namelist())
    if ext == ".7z":
        with py7zr.SevenZipFile(filepath, "r") as sz:
            return _normalize_names(sz.getnames())
    if ext == ".rar":
        with rarfile.RarFile(filepath, "r") as rf:
            return _normalize_names([info.filename for info in rf.infolist()])
    raise ValueError(f"Unsupported archive format: {ext}")


def _read_archive_member(filepath: Path, member: str) -> bytes:
    ext = filepath.suffix.lower()
    if ext == ".zip":
        with zipfile.ZipFile(filepath, "r") as zf:
            return zf.read(member)
    if ext == ".7z":
        with py7zr.SevenZipFile(filepath, "r") as sz:
            return sz.read(targets=[member])[member].read()
    if ext == ".rar":
        with rarfile.RarFile(filepath, "r") as rf:
            return rf.read(member)
    raise ValueError(f"Unsupported archive format: {ext}")


def _discover_manifest_options(names: list[str], manifest: ModManifest) -> dict[str, list[str]]:
    options: dict[str, list[str]] = {}
    for feature in manifest.features:
        prefix = feature.directory + "/"
        found: set[str] = set()
        for name in names:
            if name.startswith(prefix) and not name.endswith("/"):
                rest = name[len(prefix):]
                parts = rest.split("/")
                if len(parts) >= 2:
                    found.add(parts[0])
        options[feature.name] = sorted(found, key=str.lower)
    return options


def _default_feature_selections(
    manifest: ModManifest,
    options_by_feature: dict[str, list[str]],
    requested: dict[str, str],
) -> dict[str, str | None]:
    selections: dict[str, str | None] = {}
    for feature in manifest.features:
        available = options_by_feature.get(feature.name, [])
        if feature.name in requested:
            chosen = requested[feature.name]
            if chosen not in available:
                raise ValueError(
                    f"Invalid selection for {feature.name!r}: {chosen!r}. "
                    f"Available options: {available}"
                )
            selections[feature.name] = chosen
            continue
        if available:
            selections[feature.name] = available[0]
        else:
            selections[feature.name] = None
        if not feature.optional and selections[feature.name] is None:
            raise ValueError(f"Required feature {feature.name!r} has no available options")
    return selections


def _build_selected_members(
    archive_label: str,
    names: list[str],
    manifest: ModManifest | None,
    feature_selections: dict[str, str | None],
) -> list[str]:
    if manifest is None:
        return [name for name in names if not name.endswith("/")]

    selected: dict[str, str] = {}
    if manifest.common_files_dir:
        prefix = manifest.common_files_dir + "/"
        for name in names:
            if name.startswith(prefix) and not name.endswith("/"):
                selected[name] = name

    for feature in manifest.features:
        chosen = feature_selections.get(feature.name)
        if chosen is None:
            continue
        prefix = f"{feature.directory}/{chosen}/"
        for name in names:
            if name.startswith(prefix) and not name.endswith("/"):
                selected[name] = name

    if not selected:
        raise ValueError(f"No installable files found in {archive_label}")
    return sorted(selected)


def _decode_mod_json(data: bytes) -> dict[tuple[int, int], str]:
    mod_data = json.loads(data.decode("utf-8-sig"))
    filenames: dict[tuple[int, int], str] = {}
    for entry in mod_data.get("files", []):
        # Validate the base64 payloads so prototype behavior matches real mod data.
        if "f_extradata" in entry:
            base64.b64decode(entry["f_extradata"])
        if "r_extradata" in entry:
            base64.b64decode(entry["r_extradata"])
        if (
            "name_hash" not in entry
            or "tkid_hash" not in entry
            or "filename" not in entry
        ):
            continue
        filenames[(entry["name_hash"], entry["tkid_hash"])] = entry["filename"]
    return filenames


def _iter_fdata_entries(data: bytes) -> list[tuple[int, tuple[int, int]]]:
    if data[:8] == b"PDRK0000":
        pos = 0x10
    else:
        pos = 0

    entries: list[tuple[int, tuple[int, int]]] = []
    end = len(data)
    while pos < end:
        offset = pos
        magic = data[pos:pos + 8]
        pos += 8
        if magic != b"IDRK0000":
            pos = max(offset - 0x18, 0)
            while pos < end and data[pos:pos + 8] != b"IDRK0000":
                pos += 1
            if pos >= end:
                break
            offset = pos
            pos += 8
        entry_size, cmp_size, unc_size = struct.unpack_from("<3Q", data, pos)
        pos += 24
        entry_type, name_hash, tkid_hash, flags = struct.unpack_from("<4I", data, pos)
        _ = (entry_type, flags, cmp_size, unc_size)
        pos += 16
        entries.append((offset, (name_hash, tkid_hash)))
        entry_end = offset + entry_size
        if entry_end % 0x10:
            entry_end += 0x10 - (entry_end % 0x10)
        pos = entry_end
    return entries


def _read_fdata_entry(data: bytes, offset: int) -> tuple[bytes, str]:
    if data[offset:offset + 8] != b"IDRK0000":
        raise ValueError(f"Invalid IDRK entry offset: {offset}")
    pos = offset + 8
    entry_size, cmp_size, unc_size = struct.unpack_from("<3Q", data, pos)
    pos += 24
    entry_type, file_hash, tkid_hash, flags = struct.unpack_from("<4I", data, pos)
    _ = entry_type
    pos += 16
    metadata_size = entry_size - cmp_size - 0x30
    pos += metadata_size

    if cmp_size == unc_size:
        payload = data[pos:pos + unc_size]
    else:
        out = bytearray(unc_size)
        unc_offset = 0
        while unc_offset < unc_size:
            if flags & 0x100000:
                zsize = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            else:
                zsize, _unk0 = struct.unpack_from("<HQ", data, pos)
                pos += 10
            chunk = zlib.decompress(data[pos:pos + zsize])
            pos += zsize
            out[unc_offset:unc_offset + len(chunk)] = chunk
            unc_offset += len(chunk)
        payload = bytes(out)

    ext = TKID_EXTENSIONS.get(tkid_hash, hex(tkid_hash))
    filename = f"0x{file_hash:08X}.{ext}"
    return payload, filename


def _sanitize_mod_dir_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\\\|?*]', "_", name).strip().rstrip(".")
    return cleaned or "ConvertedMod"


def sanitize_mod_dir_name(name: str) -> str:
    return _sanitize_mod_dir_name(name)


def select_manifest_members(
    archive_label: str,
    names: list[str],
    manifest: ModManifest | None,
    feature_selections: dict[str, str | None],
) -> list[str]:
    return _build_selected_members(archive_label, names, manifest, feature_selections)


def convert_selected_members_to_loose(
    archive_path: str | Path,
    selected_members: list[str],
    *,
    mod_name: str | None = None,
    manifest: ModManifest | None = None,
    feature_selections: dict[str, str | None] | None = None,
) -> ConversionResult:
    archive = Path(archive_path)
    if archive.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported archive format: {archive.suffix}")

    loose_files: list[LooseFile] = []
    seen_names: dict[str, str] = {}
    installable_found = False

    for member in selected_members:
        if member.endswith(".yumiamod.json"):
            continue

        if member.endswith(".fdata"):
            installable_found = True
            stem = member[:-6]
            json_member = stem + ".yumiamod.json"
            fdata_bytes = _read_archive_member(archive, member)
            filename_overrides: dict[tuple[int, int], str] = {}
            if json_member in selected_members:
                filename_overrides = _decode_mod_json(_read_archive_member(archive, json_member))

            for offset, key in _iter_fdata_entries(fdata_bytes):
                payload, fallback_name = _read_fdata_entry(fdata_bytes, offset)
                filename = filename_overrides.get(key, fallback_name)
                lowered = filename.lower()
                if lowered in seen_names:
                    raise ValueError(
                        f"Loose filename collision in {archive.name}: "
                        f"{filename} from {member} duplicates {seen_names[lowered]}"
                    )
                seen_names[lowered] = member
                loose_files.append(
                    LooseFile(
                        filename=filename,
                        source_member=member,
                        data=payload,
                    )
                )
            continue

        if is_loose_asset_member(member):
            installable_found = True
            filename = Path(member).name
            lowered = filename.lower()
            if lowered in seen_names:
                raise ValueError(
                    f"Loose filename collision in {archive.name}: "
                    f"{filename} from {member} duplicates {seen_names[lowered]}"
                )
            seen_names[lowered] = member
            loose_files.append(
                LooseFile(
                    filename=filename,
                    source_member=member,
                    data=_read_archive_member(archive, member),
                )
            )

    if not installable_found or not loose_files:
        raise ValueError(f"No installable loose payloads found in {archive.name}")

    return ConversionResult(
        archive_path=archive,
        manifest=manifest,
        mod_name=mod_name or archive.stem,
        selected_features=feature_selections or {},
        files=loose_files,
    )


def convert_archive_to_loose(
    archive_path: str | Path,
    feature_overrides: dict[str, str] | None = None,
) -> ConversionResult:
    archive = Path(archive_path)
    if archive.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported archive format: {archive.suffix}")

    names = _list_archive_names(archive)
    manifest: ModManifest | None = None
    feature_selections: dict[str, str | None] = {}
    if MANIFEST_FILENAME in names:
        manifest = parse_manifest(_read_archive_member(archive, MANIFEST_FILENAME))
        options = _discover_manifest_options(names, manifest)
        feature_selections = _default_feature_selections(
            manifest,
            options,
            feature_overrides or {},
        )

    selected_members = _build_selected_members(archive.name, names, manifest, feature_selections)
    mod_name = manifest.mod_name if manifest and manifest.mod_name else archive.stem
    return convert_selected_members_to_loose(
        archive,
        selected_members,
        mod_name=mod_name,
        manifest=manifest,
        feature_selections=feature_selections,
    )


def write_conversion_result(result: ConversionResult, output_root: str | Path) -> Path:
    output_root = Path(output_root)
    mod_dir = output_root / _sanitize_mod_dir_name(result.mod_name)
    mod_dir.mkdir(parents=True, exist_ok=True)
    for item in result.files:
        (mod_dir / item.filename).write_bytes(item.data)
    return mod_dir


def _parse_feature_args(raw_values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw in raw_values:
        if "=" not in raw:
            raise ValueError(f"Invalid --feature value: {raw!r}. Expected Name=Option")
        name, value = raw.split("=", 1)
        parsed[name.strip()] = value.strip()
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prototype Yumia-to-LooseFileLoader archive converter"
    )
    parser.add_argument("archive", help="Path to the source mod archive")
    parser.add_argument(
        "--feature",
        action="append",
        default=[],
        help="Manifest selection in the form FeatureName=OptionName",
    )
    parser.add_argument(
        "--output-dir",
        help="If set, write the converted loose files under this directory",
    )
    args = parser.parse_args()

    result = convert_archive_to_loose(
        args.archive,
        feature_overrides=_parse_feature_args(args.feature),
    )

    print(f"Archive: {result.archive_path}")
    print(f"Mod name: {result.mod_name}")
    if result.selected_features:
        print("Selected features:")
        for feature, option in result.selected_features.items():
            print(f"  {feature}: {option}")
    print(f"Loose files: {len(result.files)}")
    for item in result.files:
        print(f"  {item.filename}  <=  {item.source_member}")

    if args.output_dir:
        mod_dir = write_conversion_result(result, args.output_dir)
        print(f"Wrote files to: {mod_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

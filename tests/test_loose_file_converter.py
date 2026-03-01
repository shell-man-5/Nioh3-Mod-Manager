import json
import struct
import zipfile
from pathlib import Path

from loose_file_converter import convert_archive_to_loose, write_conversion_result


def _make_fdata_entry(
    payload: bytes,
    *,
    name_hash: int,
    tkid_hash: int,
    entry_type: int = 8,
    extradata: bytes = b"",
) -> bytes:
    entry_size = 0x30 + len(extradata) + len(payload)
    entry = bytearray()
    entry.extend(b"IDRK0000")
    entry.extend(struct.pack("<3Q", entry_size, len(payload), len(payload)))
    entry.extend(struct.pack("<4I", entry_type, name_hash, tkid_hash, 0))
    entry.extend(extradata)
    entry.extend(payload)
    if len(entry) % 0x10:
        entry.extend(b"\x00" * (0x10 - (len(entry) % 0x10)))
    return bytes(entry)


def _make_fdata(entries: list[bytes]) -> bytes:
    body = b"".join(entries)
    return b"PDRK0000" + struct.pack("<2I", 0x10, len(body) + 0x10) + body


def test_convert_manifest_archive_to_loose_files(tmp_path: Path):
    archive_path = tmp_path / "sample_mod.zip"
    expected_payload = b"fake texture payload"
    name_hash = 0x1234ABCD
    tkid_hash = 0xAFBEC60C  # g1t

    manifest = {
        "mod_manager_version": "1.0",
        "mod_name": "Sample Loose Output",
        "common_files_dir": "common",
        "features": [
            {"name": "Skin", "directory": "skin", "optional": False},
        ],
    }
    yumia_json = {
        "fdata_hash": 0x88888888,
        "files": [
            {
                "filename": "0x1234ABCD.g1t",
                "name_hash": name_hash,
                "tkid_hash": tkid_hash,
                "entry_type": 8,
                "string_size": 13,
                "f_extradata": "",
                "r_extradata": "",
            }
        ],
    }
    fdata = _make_fdata(
        [_make_fdata_entry(expected_payload, name_hash=name_hash, tkid_hash=tkid_hash)]
    )

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("nioh3modmanifest.json", json.dumps(manifest))
        zf.writestr("common/0x88888888.yumiamod.json", json.dumps(yumia_json))
        zf.writestr("common/0x88888888.fdata", fdata)
        zf.writestr("skin/Normal/ignored.txt", b"this should still copy through")

    result = convert_archive_to_loose(archive_path, feature_overrides={"Skin": "Normal"})

    assert result.mod_name == "Sample Loose Output"
    assert result.selected_features == {"Skin": "Normal"}
    filenames = {item.filename for item in result.files}
    assert "0x1234ABCD.g1t" in filenames
    assert "ignored.txt" not in filenames

    out_dir = tmp_path / "mods"
    mod_dir = write_conversion_result(result, out_dir)
    assert (mod_dir / "0x1234ABCD.g1t").read_bytes() == expected_payload
    assert not (mod_dir / "ignored.txt").exists()

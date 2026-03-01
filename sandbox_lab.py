from __future__ import annotations

import argparse
import difflib
import json
import shutil
import struct
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from backend_manager import LEGACY_MANIFEST_FILENAME, STATE_MANIFEST_FILENAME

REPO_ROOT = Path(__file__).resolve().parent
SANDBOX_ROOT = REPO_ROOT / "sandbox_envs"
YUMIA_EXE_NAME = "yumia_mod_insert_into_rdb.exe"

TKID_G1T = 0xAFBEC60C
TKID_G1M = 0x563BDEF1


@dataclass(frozen=True)
class SandboxEnvironment:
    name: str
    description: str


ENVIRONMENTS = [
    SandboxEnvironment(
        name="empty_no_backend",
        description="No yumia, no LooseFileLoader, no installed mods.",
    ),
    SandboxEnvironment(
        name="yumia_only_clean",
        description="Mock yumia available, no installed mods.",
    ),
    SandboxEnvironment(
        name="yumia_only_with_mods",
        description="Mock yumia available, legacy and manifest yumia installs active.",
    ),
    SandboxEnvironment(
        name="both_with_yumia_mods",
        description="Mock yumia and LooseFileLoader available, active yumia installs ready for migration.",
    ),
    SandboxEnvironment(
        name="loose_only_with_loose_mods",
        description="LooseFileLoader only, active loose installs.",
    ),
    SandboxEnvironment(
        name="current_like_both_with_loose_mods",
        description="Both backends available, active loose installs and no active yumia installs.",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sandbox Nioh 3 GUI test environments")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build sandbox environments")
    build_parser.add_argument("env", nargs="*", help="Specific environment names to build")
    build_parser.add_argument("--rebuild", action="store_true", help="Delete and recreate the selected environments")

    run_parser = subparsers.add_parser("run", help="Launch the app against a sandbox environment")
    run_parser.add_argument("env", help="Environment name to launch")
    run_parser.add_argument("--rebuild", action="store_true", help="Rebuild the environment before launch")

    subparsers.add_parser("list", help="List available sandbox environments")
    return parser.parse_args()


def unknown_environment_error(env_name: str) -> str:
    known = [env.name for env in ENVIRONMENTS]
    suggestion = difflib.get_close_matches(env_name, known, n=1, cutoff=0.6)
    if suggestion:
        return (
            f"Unknown sandbox environment: {env_name}\n"
            f"Did you mean: {suggestion[0]}?"
        )
    return (
        f"Unknown sandbox environment: {env_name}\n"
        f"Available environments: {', '.join(known)}"
    )


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_bytes(path: Path, data: bytes) -> None:
    ensure_parent(path)
    path.write_bytes(data)


def write_text(path: Path, text: str) -> None:
    ensure_parent(path)
    path.write_text(text, encoding="utf-8")


def make_fdata_entry(
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


def make_fdata(entries: list[bytes]) -> bytes:
    body = b"".join(entries)
    return b"PDRK0000" + struct.pack("<2I", 0x10, len(body) + 0x10) + body


def make_yumia_json(filename: str, name_hash: int, tkid_hash: int) -> bytes:
    return json.dumps(
        {
            "files": [
                {
                    "filename": filename,
                    "name_hash": name_hash,
                    "tkid_hash": tkid_hash,
                    "entry_type": 8,
                    "f_extradata": "",
                    "r_extradata": "",
                }
            ]
        }
    ).encode("utf-8")


def make_yumia_pair(
    stem_hash: int,
    *,
    loose_filename: str,
    name_hash: int,
    tkid_hash: int,
    payload: bytes,
) -> dict[str, bytes]:
    stem = f"0x{stem_hash:08X}"
    return {
        f"{stem}.fdata": make_fdata(
            [make_fdata_entry(payload, name_hash=name_hash, tkid_hash=tkid_hash)]
        ),
        f"{stem}.yumiamod.json": make_yumia_json(loose_filename, name_hash, tkid_hash),
    }


def write_zip(path: Path, members: dict[str, bytes | str]) -> None:
    ensure_parent(path)
    with zipfile.ZipFile(path, "w") as zf:
        for member_name, data in members.items():
            payload = data.encode("utf-8") if isinstance(data, str) else data
            zf.writestr(member_name, payload)


def add_core_game_files(package_dir: Path) -> None:
    write_bytes(package_dir / "root.rdb", b"root rdb")
    write_bytes(package_dir / "root.rdx", b"root rdx")


def add_rdb_backups(package_dir: Path) -> None:
    add_core_game_files(package_dir)
    shutil.copy2(package_dir / "root.rdb", package_dir / "root.rdb.original")
    shutil.copy2(package_dir / "root.rdx", package_dir / "root.rdx.original")


def add_mock_yumia(package_dir: Path) -> None:
    write_text(package_dir / YUMIA_EXE_NAME, "mock yumia marker\n")


def add_loose_loader(game_root: Path) -> None:
    write_bytes(game_root / "DINPUT8.dll", b"dinput8")
    write_bytes(game_root / "plugins" / "LooseFileLoader.dll", b"loose loader")
    write_text(game_root / "plugins" / "LooseFileLoader.ini", "[LooseFileLoader]\nEnableAssetLoadingLog=0\n")


def create_sample_archives(downloads_dir: Path) -> None:
    legacy_members = make_yumia_pair(
        0x10000001,
        loose_filename="0x11000001.g1t",
        name_hash=0x11000001,
        tkid_hash=TKID_G1T,
        payload=b"legacy texture",
    )
    write_zip(downloads_dir / "01 Sandbox Yumia Legacy.zip", legacy_members)

    option_a = make_yumia_pair(
        0x12000001,
        loose_filename="0x12100001.g1m",
        name_hash=0x12100001,
        tkid_hash=TKID_G1M,
        payload=b"type 1 model",
    )
    option_b = make_yumia_pair(
        0x12000002,
        loose_filename="0x12100002.g1m",
        name_hash=0x12100002,
        tkid_hash=TKID_G1M,
        payload=b"type 2 model",
    )
    option_members: dict[str, bytes] = {}
    for name, data in option_a.items():
        option_members[f"Type 1/package/{name}"] = data
    for name, data in option_b.items():
        option_members[f"Type 2/package/{name}"] = data
    write_zip(downloads_dir / "02 Sandbox Yumia Options.zip", option_members)

    manifest = {
        "mod_manager_version": "1.0",
        "mod_name": "Sandbox Yumia Manifest",
        "author": "Sandbox",
        "version": "1.0",
        "url": "https://example.invalid/sandbox-manifest",
        "common_files_dir": "common",
        "features": [
            {
                "name": "Skin",
                "directory": "skin",
                "optional": False,
            }
        ],
    }
    common_pair = make_yumia_pair(
        0x13000001,
        loose_filename="0x13100001.g1t",
        name_hash=0x13100001,
        tkid_hash=TKID_G1T,
        payload=b"common texture",
    )
    normal_pair = make_yumia_pair(
        0x13000010,
        loose_filename="0x13100010.g1m",
        name_hash=0x13100010,
        tkid_hash=TKID_G1M,
        payload=b"normal model",
    )
    wet_pair = make_yumia_pair(
        0x13000020,
        loose_filename="0x13100020.g1m",
        name_hash=0x13100020,
        tkid_hash=TKID_G1M,
        payload=b"wet model",
    )
    manifest_members: dict[str, bytes | str] = {
        "nioh3modmanifest.json": json.dumps(manifest),
    }
    for name, data in common_pair.items():
        manifest_members[f"common/{name}"] = data
    for name, data in normal_pair.items():
        manifest_members[f"skin/Normal/{name}"] = data
    for name, data in wet_pair.items():
        manifest_members[f"skin/Wet/{name}"] = data
    write_zip(downloads_dir / "03 Sandbox Yumia Manifest.zip", manifest_members)

    write_zip(
        downloads_dir / "04 Sandbox Loose Bundle.zip",
        {
            "0x14000001.g1t": b"bundle texture",
            "0x14000002.g1m": b"bundle model",
        },
    )

    write_zip(
        downloads_dir / "05 Sandbox Loose Components.zip",
        {
            "Lone Wolf/0x15000001.g1m": b"lone wolf",
            "Thief Waistguard/0x15000002.g1m": b"thief waistguard",
        },
    )

    loose_manifest = {
        "mod_manager_version": "1.0",
        "mod_name": "Sandbox Loose Manifest",
        "author": "Sandbox",
        "version": "1.0",
        "common_files_dir": "common",
        "features": [
            {
                "name": "Variant",
                "directory": "variant",
                "optional": False,
            }
        ],
    }
    write_zip(
        downloads_dir / "06 Sandbox Manifest Loose.zip",
        {
            "nioh3modmanifest.json": json.dumps(loose_manifest),
            "common/0x16000001.g1t": b"common loose texture",
            "variant/Blue/0x16000002.g1m": b"blue loose model",
            "variant/Red/0x16000003.g1m": b"red loose model",
        },
    )


def write_state(package_dir: Path, records: dict[str, dict[str, object]]) -> None:
    write_text(
        package_dir / STATE_MANIFEST_FILENAME,
        json.dumps({"version": 2, "records": records}, indent=2),
    )


def write_legacy_manifest(downloads_dir: Path, records: dict[str, dict[str, object]]) -> None:
    write_text(downloads_dir / LEGACY_MANIFEST_FILENAME, json.dumps(records, indent=2))


def make_yumia_installed_records(package_dir: Path, downloads_dir: Path) -> dict[str, dict[str, object]]:
    legacy_files = [
        "0x12000002.fdata",
        "0x12000002.yumiamod.json",
    ]
    manifest_files = [
        "0x13000001.fdata",
        "0x13000001.yumiamod.json",
        "0x13000010.fdata",
        "0x13000010.yumiamod.json",
    ]
    for filename in legacy_files + manifest_files:
        write_bytes(package_dir / filename, filename.encode("ascii"))

    write_legacy_manifest(
        downloads_dir,
        {
            "02 Sandbox Yumia Options.zip": {
                "archive_filename": "02 Sandbox Yumia Options.zip",
                "option_name": "Type 2",
                "installed_files": legacy_files,
            },
            "03 Sandbox Yumia Manifest.zip": {
                "archive_filename": "03 Sandbox Yumia Manifest.zip",
                "option_name": "Skin: Normal",
                "installed_files": manifest_files,
            },
        },
    )
    return {
        "02 Sandbox Yumia Options.zip": {
            "archive_filename": "02 Sandbox Yumia Options.zip",
            "backend": "yumia",
            "install_kind": "legacy",
            "display_option_summary": "Type 2",
            "legacy_option_name": "Type 2",
            "feature_selections": None,
            "installed_paths": [f"package/{name}" for name in legacy_files],
            "loose_mod_dir": None,
        },
        "03 Sandbox Yumia Manifest.zip": {
            "archive_filename": "03 Sandbox Yumia Manifest.zip",
            "backend": "yumia",
            "install_kind": "manifest",
            "display_option_summary": "Skin: Normal",
            "legacy_option_name": None,
            "feature_selections": {"Skin": "Normal"},
            "installed_paths": [f"package/{name}" for name in manifest_files],
            "loose_mod_dir": None,
        },
    }


def make_loose_installed_records(game_root: Path) -> dict[str, dict[str, object]]:
    loose_dir = game_root / "mods"

    manifest_folder = loose_dir / "Sandbox Loose Manifest"
    write_bytes(manifest_folder / "0x16000001.g1t", b"common loose texture")
    write_bytes(manifest_folder / "0x16000002.g1m", b"blue loose model")

    component_folder = loose_dir / "05 Sandbox Loose Components"
    write_bytes(component_folder / "0x15000001.g1m", b"lone wolf")
    write_bytes(component_folder / "0x15000002.g1m", b"thief waistguard")

    return {
        "06 Sandbox Manifest Loose.zip": {
            "archive_filename": "06 Sandbox Manifest Loose.zip",
            "backend": "loose",
            "install_kind": "manifest",
            "display_option_summary": "Variant: Blue",
            "legacy_option_name": None,
            "feature_selections": {"Variant": "Blue"},
            "installed_paths": [
                "mods/Sandbox Loose Manifest/0x16000001.g1t",
                "mods/Sandbox Loose Manifest/0x16000002.g1m",
            ],
            "loose_mod_dir": "mods/Sandbox Loose Manifest",
        },
        "05 Sandbox Loose Components.zip": {
            "archive_filename": "05 Sandbox Loose Components.zip",
            "backend": "loose",
            "install_kind": "legacy",
            "display_option_summary": "Lone Wolf; Thief Waistguard",
            "legacy_option_name": None,
            "feature_selections": None,
            "installed_paths": [
                "mods/05 Sandbox Loose Components/0x15000001.g1m",
                "mods/05 Sandbox Loose Components/0x15000002.g1m",
            ],
            "loose_mod_dir": "mods/05 Sandbox Loose Components",
        },
    }


def build_environment(env: SandboxEnvironment, rebuild: bool) -> Path:
    env_root = SANDBOX_ROOT / env.name
    if rebuild and env_root.exists():
        shutil.rmtree(env_root)

    downloads_dir = env_root / "downloads"
    package_dir = env_root / "game" / "Nioh3" / "package"
    game_root = package_dir.parent
    (game_root / "mods").mkdir(parents=True, exist_ok=True)
    create_sample_archives(downloads_dir)
    add_core_game_files(package_dir)

    state_records: dict[str, dict[str, object]] = {}

    if env.name in {"yumia_only_clean", "yumia_only_with_mods", "both_with_yumia_mods", "current_like_both_with_loose_mods"}:
        add_mock_yumia(package_dir)

    if env.name in {"both_with_yumia_mods", "loose_only_with_loose_mods", "current_like_both_with_loose_mods"}:
        add_loose_loader(game_root)

    if env.name in {"yumia_only_with_mods", "both_with_yumia_mods"}:
        add_rdb_backups(package_dir)
        state_records.update(make_yumia_installed_records(package_dir, downloads_dir))

    if env.name in {"loose_only_with_loose_mods", "current_like_both_with_loose_mods"}:
        state_records.update(make_loose_installed_records(game_root))

    if state_records:
        write_state(package_dir, state_records)

    return env_root


def build_selected_environments(selected: list[str], rebuild: bool) -> list[Path]:
    selected_names = set(selected)
    targets = [
        env for env in ENVIRONMENTS
        if not selected_names or env.name in selected_names
    ]
    if selected_names:
        known = {env.name for env in ENVIRONMENTS}
        unknown = sorted(selected_names - known)
        if unknown:
            if len(unknown) == 1:
                raise SystemExit(unknown_environment_error(unknown[0]))
            raise SystemExit(
                "Unknown sandbox environment(s): "
                + ", ".join(unknown)
                + "\nAvailable environments: "
                + ", ".join(env.name for env in ENVIRONMENTS)
            )
    return [build_environment(env, rebuild=rebuild) for env in targets]


def launch_environment(env_name: str, rebuild: bool) -> None:
    env_map = {env.name: env for env in ENVIRONMENTS}
    if env_name not in env_map:
        raise SystemExit(unknown_environment_error(env_name))

    env_root = build_environment(env_map[env_name], rebuild=rebuild)
    downloads_dir = env_root / "downloads"
    package_dir = env_root / "game" / "Nioh3" / "package"
    args = [
        sys.executable,
        str(REPO_ROOT / "main.py"),
        "--mods-dir",
        str(downloads_dir),
        "--game-package-dir",
        str(package_dir),
        "--settings-app",
        f"Nioh3ModManagerSandbox.{env_name}",
        "--no-persist-settings",
        "--window-title-suffix",
        f"[Sandbox: {env_name}]",
        "--mock-yumia",
    ]
    subprocess.Popen(args, cwd=str(REPO_ROOT))


def main() -> int:
    args = parse_args()

    if args.command == "list":
        for env in ENVIRONMENTS:
            print(f"{env.name}: {env.description}")
        return 0

    if args.command == "build":
        built = build_selected_environments(args.env, rebuild=args.rebuild)
        for path in built:
            print(path)
        return 0

    if args.command == "run":
        launch_environment(args.env, rebuild=args.rebuild)
        return 0

    raise SystemExit(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

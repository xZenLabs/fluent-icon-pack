"""Run with python3 -m unittest discover -s scripts -p 'test_*.py'."""

import importlib.util
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image, PngImagePlugin


spec = importlib.util.spec_from_file_location("builder", Path(__file__).with_name("build-zen-packs.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><path d="M0 0h48v48z"/></svg>'


class BuildPacksTest(unittest.TestCase):
    def test_repository_packs_preserve_sources_and_build_supported_lookups(self):
        required = set("""
            app_launcher app_menu appbar.menu appbar.search appbar.settings appbar.tools
            appbar.textsize archive atom avatar blocks book_closed book_open bookshelf
            calendar cloud coverflow database education external_link folder folder_open
            font fonts gallery globe grid grid_slide home library lookup.dictionary
            lookup_dictionary lookup.highlight lookup_highlight lookup.vocab lookup_vocab
            lookup.vocab_remove lookup_vocab_remove more_vertical network open_box
            paint_palette qa_plugin quick_cloud quick_localsend quick_lockdown quick_opds
            quick_puzzle quick_rotate quick_screenshot quick_search quick_stats_calendar
            quick_stats_progress quick_sync quick_wifi quicksettings restart share
            shield_check shield_warning skip_left skip_right speed sun tab_authors
            tab_books tab_collections tab_continue tab_favorites tab_filebrowser tab_history
            tab_left tab_manga tab_news tab_right tab_series tab_stats tab_to_be_read
            tab_tools tab_translate tab_vocab tablet terminal time timer toc tuning upload
            browser categories checkmark chevron.left chevron.right chevron.up clear close
            date  downgrade download downloads  games icon_packs packages
            plugin productivity reference refresh reinstall remove remove_queue scan_plugins
            search settings settings_advanced sources star star.filled theme title
            unverified update upgrade utility verified wallabag wallpaper zenpm
            quick_aa quick_battery quick_bluetooth quick_calibre quick_calibre_dark quick_chess
            quick_connections quick_crossword quick_exit quick_filebrowser quick_incognito quick_nightmode
            quick_notion quick_quickrss quick_restart quick_sleep quick_streak quick_usb
            quick_zen quick_zlib tab_exit tab_folder tab_tags
        """.split())
        mono_required = set("""
            align.auto align.center align.justify align.left align.right
            battery battery_full battery_half battery_low battery_charging quick_battery
            quick_bluetooth quick_exit quick_incognito quick_nightmode quick_streak
            quick_usb sleep quick_sleep screensaver tab_tags bell_off bug calculator
            compass cpu disable enable filter info lightning patch qrcode sort_asc
            sort_desc trash usb warmth column.one column.two column.three
            wifi.open.0 wifi.open.25 wifi.open.50 wifi.open.75 wifi.secure.100
        """.split())
        expected_counts = {"Fluent Color": 222, "Fluent Mono": 276}
        metadata = json.loads((builder.ROOT / "pack.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            packs = list(builder.pack_sources(builder.ROOT, output))
            self.assertEqual({pack.name for pack, _ in packs}, set(expected_counts))
            cases = [(builder.ROOT, metadata["version"], packs),
                     (builder.ROOT, "2.3.4", packs)]
            cases.extend((pack, "3.4.5", [(pack, directories)]) for pack, directories in packs)
            for source, expected_version, selected in cases:
                with self.subTest(source=source, version=expected_version), patch("sys.argv", [
                        "build-zen-packs.py", str(source), "--output", str(output),
                        "--version", expected_version]):
                    builder.main()
                    for pack, directories in selected:
                        expected = dict(metadata, id=builder.zen_id(pack.name),
                                        name=pack.name + " for ZenOS", version=expected_version,
                                        color=pack.name != "Fluent Mono")
                        style_metadata = json.loads((pack / "pack.json").read_text())
                        self.assertEqual(dict(style_metadata, version=expected_version), expected)
                        sources = {path.name: path for directory in directories
                                   for path in directory.rglob("*.svg")}
                        provenance_path = pack / "icon-sources.json"
                        if not provenance_path.exists():
                            provenance_path = builder.ROOT / "icon-sources.json"
                        provenance = json.loads(provenance_path.read_text())
                        destination = output / (expected["id"] + ".zip")
                        with zipfile.ZipFile(destination) as archive:
                            prefix = expected["id"] + "/"
                            names = {Path(name).stem for name in archive.namelist()
                                     if name.endswith(".svg")}
                            self.assertEqual(len(names), expected_counts[pack.name])
                            required_names = required | (mono_required if pack.name == "Fluent Mono" else set())
                            self.assertFalse(required_names - names, sorted(required_names - names))
                            self.assertFalse(set(provenance["bundled_fallbacks"]) & names)
                            self.assertNotIn("tab_spacer", names)
                            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
                            self.assertIsNone(archive.testzip())
                            self.assertFalse(any(name.endswith(".lua") for name in archive.namelist()))
                            for name, path in sources.items():
                                self.assertEqual(archive.read(prefix + name), path.read_bytes())
                            for target, source_name in builder.ALIASES.items():
                                if target + ".svg" not in sources and source_name in names:
                                    self.assertEqual(archive.read(prefix + target + ".svg"),
                                                     archive.read(prefix + source_name + ".svg"))
                            self.assertEqual(json.loads(archive.read(prefix + "pack.json")), expected)
                            self.assertEqual(archive.read(prefix + "LICENSE-ICONS.txt"),
                                             (pack / "LICENSE-ICONS.txt").read_bytes())

    def test_mono_wifi_strength_uses_fluents_reversed_numbering(self):
        # Fluent's Wi-Fi designs run from strongest (1) to weakest (4).
        provenance = json.loads((builder.ROOT / "Fluent Mono/icon-sources.json").read_text())
        recorded = {Path(relative).stem: value["icon"]
                    for relative, value in provenance["icons"].items()}
        for target, source in {
                "quick_wifi": "wifi-1-24-regular",
                "wifi": "wifi-1-24-regular",
                "wifi.open.0": "wifi-off-24-regular",
                "wifi.open.25": "wifi-4-24-regular",
                "wifi.open.50": "wifi-3-24-regular",
                "wifi.open.75": "wifi-2-24-regular",
                "wifi.open.100": "wifi-1-24-regular"}.items():
            with self.subTest(icon=target):
                self.assertEqual(recorded[target], "fluent:" + source)

    def test_sources_match_provenance_and_svg_references_resolve(self):
        packs = builder.pack_sources(builder.ROOT, builder.ROOT / "dist")
        for pack, directories in packs:
            provenance_path = pack / "icon-sources.json"
            if not provenance_path.exists():
                provenance_path = builder.ROOT / "icon-sources.json"
            provenance = json.loads(provenance_path.read_text())
            paths = {path.relative_to(builder.ROOT).as_posix()
                     for directory in directories for path in directory.rglob("*.svg")}
            self.assertEqual(paths, set(provenance["icons"]))
            self.assertEqual(len(provenance["revision"]), 40)
            for relative, recorded in provenance["icons"].items():
                with self.subTest(icon=relative):
                    data = (builder.ROOT / relative).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), recorded["sha256"])
                    prefixes = {provenance["prefix"]} | {
                        item["prefix"] for item in provenance.get("additional_sources", [])
                    }
                    self.assertIn(recorded["icon"].split(":", 1)[0], prefixes)
                    root = ET.fromstring(data)
                    self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                    self.assertTrue(all(float(value) > 0 for value in root.attrib["viewBox"].split()[2:]))
                    if pack.name == "Fluent Mono":
                        self.assertEqual(root.attrib["color"], "#000000")
                    ids = [element.attrib["id"] for element in root.iter() if "id" in element.attrib]
                    self.assertEqual(len(ids), len(set(ids)))
                    for element in root.iter():
                        self.assertNotIn(element.tag.rsplit("}", 1)[-1], ("script", "foreignObject", "image"))
                        for key, value in element.attrib.items():
                            self.assertFalse(key.lower().startswith("on"))
                            if key.rsplit("}", 1)[-1] == "href":
                                self.assertTrue(value.startswith("#"))
                                self.assertIn(value[1:], ids)
                            for reference in re.findall(r"url\(#([^)]+)\)", value):
                                self.assertIn(reference, ids)
                    if "gradient" in recorded:
                        stops = root.findall(".//{http://www.w3.org/2000/svg}stop")
                        self.assertEqual([stop.attrib["stop-color"] for stop in stops],
                                         recorded["gradient"])
                        self.assertNotIn(b"currentColor", data)
                    if "transform" in recorded:
                        self.assertEqual(root[0].attrib["transform"], recorded["transform"])
                    if "opacity" in recorded:
                        self.assertEqual(float(root[0].attrib["opacity"]), recorded["opacity"])

    def test_color_metadata_survives_repository_and_direct_builds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            manifest = {"version": "2.3.4", "author": "Repository artist", "color": True}
            (root / "pack.json").write_text(json.dumps(manifest))
            for name, color in (("Color", True), ("Mono", False)):
                pack = root / name
                icons = pack / "Pack Icons"
                icons.mkdir(parents=True)
                (icons / "home.svg").write_bytes(SVG)
                (pack / "pack.json").write_text(json.dumps(dict(manifest, color=color)))
            for source in (root, root / "Color", root / "Mono"):
                with self.subTest(source=source), patch("sys.argv", [
                        "build-zen-packs.py", str(source), "--output", str(output),
                        "--version", "3.4.5"]):
                    builder.main()
                    for pack, _ in builder.pack_sources(source, output):
                        pack_id = builder.zen_id(pack.name)
                        with zipfile.ZipFile(output / (pack_id + ".zip")) as archive:
                            metadata = json.loads(archive.read(pack_id + "/pack.json"))
                            self.assertIs(metadata["color"], pack.name == "Color")
                            self.assertEqual(metadata["version"], "3.4.5")
                            self.assertEqual(metadata["author"], "Repository artist")
            for manifest_path in (root / "pack.json", root / "Mono" / "pack.json"):
                for invalid in (None, "false", 0, 1, [], {}):
                    with self.subTest(manifest=manifest_path, color=invalid):
                        manifest_path.write_text(json.dumps(dict(manifest, color=invalid)))
                        with patch("sys.argv", ["build-zen-packs.py", str(root),
                                                "--output", str(output)]):
                            with patch("sys.stderr", new=StringIO()) as errors:
                                with self.assertRaises(SystemExit):
                                    builder.main()
                            self.assertIn("color must be a boolean", errors.getvalue())
                manifest_path.write_text(json.dumps(manifest))

    def test_repository_pack_json_metadata_and_cli_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack, output = root / "Pack", root / "output"
            pack.mkdir()
            (pack / "sui_menu.svg").write_bytes(SVG)
            (pack / "pack.lua").write_text(
                'return { name = "Pack", version = "1.0.0", author = "Old artist", }')
            manifest = {"schema_version": 1, "id": "zen-example", "name": "Example for ZenOS",
                        "version": "2.3.4", "author": "Repository artist"}
            (root / "pack.json").write_text(json.dumps(manifest))
            for options, expected in (([], "2.3.4"), (["--version", "3.0.0"], "3.0.0")):
                with self.subTest(options=options), patch("sys.argv", [
                        "build-zen-packs.py", str(root), "--output", str(output), *options]):
                    builder.main()
                    with zipfile.ZipFile(output / "zen-pack.zip") as archive:
                        metadata = json.loads(archive.read("zen-pack/pack.json"))
                        self.assertEqual(metadata, dict(manifest, id="zen-pack",
                                                        name="Pack for ZenOS", version=expected))
            (pack / "pack.json").write_text(json.dumps(manifest))
            with patch("sys.argv", ["build-zen-packs.py", str(pack), "--output", str(output)]):
                builder.main()
            with zipfile.ZipFile(output / "zen-example.zip") as archive:
                self.assertEqual(json.loads(archive.read("zen-example/pack.json")), manifest)
            (pack / "pack.json").write_text(json.dumps(dict(manifest, id="../unsafe")))
            with patch("sys.argv", ["build-zen-packs.py", str(pack), "--output", str(output)]):
                with self.assertRaises(SystemExit), patch("sys.stderr"):
                    builder.main()

    def test_custom_png_map_strips_metadata_and_appended_zip_preserving_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack, output = root / "input", root / "output"
            pack.mkdir()
            output.mkdir()
            marker = root / "must-not-exist"
            (pack / "pack.lua").write_text(
                f'os.execute("touch {marker}")\n'
                'return { name = "Night Owl", author = "Test artist", version = "2.3.4",\n'
                'map = { ["sui_action_homescreen"] = "house image.png", sui_search = "lens.svg", }, }\n')
            (pack / "lens.svg").write_bytes(SVG)
            pixels = Image.new("RGBA", (2, 2), (12, 34, 56, 128))
            pixels.putpixel((1, 1), (200, 150, 100, 0))
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("payload", "metadata must be discarded")
            metadata.add(b"ruSt", b"hidden private chunk")
            original = BytesIO()
            pixels.save(original, format="PNG", pnginfo=metadata, icc_profile=b"fake ICC payload")
            appended = BytesIO()
            with zipfile.ZipFile(appended, "w") as archive:
                archive.writestr("hidden.lua", "untrusted appended file")
            source = original.getvalue() + appended.getvalue()
            (pack / "house image.png").write_bytes(source)

            destination = builder.build_pack(pack, [pack], pack, output, None)

            self.assertFalse(marker.exists())
            self.assertEqual((pack / "house image.png").read_bytes(), source)
            with zipfile.ZipFile(destination) as archive:
                prefix = "zen-night-owl/"
                manifest = json.loads(archive.read(prefix + "pack.json"))
                self.assertEqual(manifest["id"], "zen-night-owl")
                self.assertEqual(manifest["version"], "2.3.4")
                self.assertEqual(manifest["author"], "Test artist")
                self.assertEqual(archive.read(prefix + "appbar.search.svg"), SVG)
                clean = archive.read(prefix + "home.png")
                self.assertEqual(clean, archive.read(prefix + "house-image.png"))
                self.assertNotIn(prefix + "pack.lua", archive.namelist())
            with Image.open(BytesIO(clean)) as image:
                self.assertEqual(image.info, {})
                self.assertEqual(image.convert("RGBA").tobytes(), pixels.tobytes())
            offset, chunks = 8, []
            while offset < len(clean):
                size = struct.unpack_from(">I", clean, offset)[0]
                chunks.append(clean[offset + 4:offset + 8])
                offset += size + 12
            self.assertEqual(offset, len(clean))
            self.assertEqual(chunks, [b"IHDR", b"IDAT", b"IEND"])

    def test_flat_pack_without_manifest_uses_svg_and_png_slot_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / "Minimal"
            output = Path(temporary) / "output"
            pack.mkdir()
            output.mkdir()
            (pack / "sui_menu.svg").write_bytes(SVG)
            Image.new("P", (1, 1)).save(pack / "sui_action_wifi_toggle.png")
            Image.new("RGBA", (1, 1)).save(pack / "HOME.PNG")
            self.assertEqual(list(builder.pack_sources(pack, output)), [(pack, [pack])])
            destination = builder.build_pack(pack, [pack], pack, output, "9.0")
            with zipfile.ZipFile(destination) as archive:
                self.assertIn("zen-minimal/appbar.menu.svg", archive.namelist())
                self.assertIn("zen-minimal/quick_wifi.png", archive.namelist())
                self.assertIn("zen-minimal/home.png", archive.namelist())
                self.assertNotIn("zen-minimal/HOME.png", archive.namelist())
                self.assertEqual(json.loads(archive.read("zen-minimal/pack.json"))["version"], "9.0")

    def test_bad_pngs_are_rejected_without_replacing_a_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / "Broken"
            output = Path(temporary) / "output"
            pack.mkdir()
            output.mkdir()
            destination = output / "zen-broken.zip"
            destination.write_bytes(b"previous release")
            valid = BytesIO()
            Image.new("RGBA", (2, 2)).save(valid, format="PNG")
            animated = BytesIO()
            Image.new("RGBA", (2, 2), "red").save(
                animated, format="PNG", save_all=True,
                append_images=[Image.new("RGBA", (2, 2), "blue")])
            for name, data in {"empty": b"", "renamed SVG": SVG,
                               "truncated": valid.getvalue()[:24], "animated": animated.getvalue()}.items():
                with self.subTest(name=name):
                    (pack / "icon.png").write_bytes(data)
                    with self.assertRaises((ValueError, OSError, SyntaxError)):
                        builder.build_pack(pack, [pack], pack, output, None)
                    self.assertEqual(destination.read_bytes(), b"previous release")
            (pack / "icon.png").write_bytes(valid.getvalue())
            with patch.object(Image, "MAX_IMAGE_PIXELS", 3):
                with self.assertRaises(Image.DecompressionBombWarning):
                    builder.clean_icon(pack / "icon.png")

    def test_duplicates_missing_maps_and_symlinks_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / "Bad"
            output = Path(temporary) / "output"
            pack.mkdir()
            output.mkdir()
            (pack / "home.svg").write_bytes(SVG)
            second = pack / "second"
            second.mkdir()
            (second / "HOME.svg").write_bytes(SVG)
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                builder.build_pack(pack, [pack], pack, output, None)
            (second / "HOME.svg").unlink()
            (pack / "pack.lua").write_text('return { map = { sui_menu = "missing.svg", }, }')
            with self.assertRaisesRegex(ValueError, "missing mapped icon"):
                builder.build_pack(pack, [pack], pack, output, None)
            (pack / "pack.lua").unlink()
            (pack / "linked.svg").symlink_to(pack / "home.svg")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                builder.build_pack(pack, [pack], pack, output, None)

    def test_discovers_mixed_variants_and_ignores_generated_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            named, plain, output = root / "Named", root / "Plain", root / "output"
            for directory in (named, plain, output):
                directory.mkdir()
                (directory / "sui_menu.svg").write_bytes(SVG)
            (named / "pack.lua").write_text('return { name = "Named", }')
            (output / "pack.lua").write_text('return { name = "Generated", }')
            self.assertEqual(list(builder.pack_sources(root, output)),
                             [(named, [named]), (plain, [plain])])
            (named / "pack.lua").write_text('return { name = computed_name(), }')
            with self.assertRaisesRegex(ValueError, "literal quoted string"):
                builder.lua_strings(named / "pack.lua")
            self.assertEqual(builder.zen_id("Lumière"), "zen-lumiere")
            self.assertRegex(builder.zen_id("夜猫"), r"^zen-[a-f0-9]{12}$")


if __name__ == "__main__":
    unittest.main()

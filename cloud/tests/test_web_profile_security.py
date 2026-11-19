"""Web profile provisioning stays inside owned test tenant directories."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paths
import provision
import tenant_files


class WebProfileBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mdagent-web-profile-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.code = self.root / "code"
        for name in (*provision.WEB_PLUGINS, "dsh-bio-agent", "dsh-bio-ui"):
            (self.code / "dsh-plugins" / name).mkdir(parents=True)
        source = self.code / "presets/md-sim-assistant"
        source.mkdir(parents=True)
        (source / "agent.cordis.yml").write_text("[]\n")
        (source / "preset.yml").write_text("name: fixture\n")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.sentinel = self.outside / "sentinel.json"
        self.sentinel.write_text('{"preserve": true}\n')
        self.new_tenant("initial")
        for object_, name, value in (
            (paths, "accounts_dir", lambda: str(self.base / "accounts")),
            (paths, "user_dsh_home", lambda username: str(self.home)),
            (paths, "code_dir", lambda: str(self.code)),
        ):
            override = patch.object(object_, name, side_effect=value)
            override.start()
            self.addCleanup(override.stop)
        environment = patch.dict(os.environ, {"MDAGENT_SKIP_UNIX": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        runtime = patch.object(provision, "ensure_web_runtime")
        runtime.start()
        self.addCleanup(runtime.stop)

    def new_tenant(self, name):
        self.base = self.root / name
        self.home = self.base / "users/alice/dsh-home"
        self.home.mkdir(parents=True, mode=0o700)

    def assert_sentinel_unchanged(self):
        self.assertEqual(self.sentinel.read_text(), '{"preserve": true}\n')

    def test_regular_profile_keeps_custom_config_and_is_idempotent(self):
        profile = self.home / "profiles/web"
        profile.mkdir(parents=True)
        (profile / "package.json").write_text(json.dumps({
            "private": True, "custom": "preserved",
            "dsh": {"profile": {"bundles": ["fixture-custom-bundle"]}},
        }))
        self.assertEqual(provision.enable_web_profile("alice"), str(profile))
        value = json.loads((profile / "package.json").read_text())
        self.assertEqual(value["custom"], "preserved")
        self.assertIn("fixture-custom-bundle", value["dsh"]["profile"]["bundles"])
        self.assertIn("dsh-remote-world", value["dsh"]["profile"]["bundles"])
        self.assertEqual((self.home / ".agent-presets/md-sim-assistant/preset.yml").read_text(), "name: fixture\n")
        self.assertEqual((self.home / "profiles/node_modules/dsh-bio-agent").resolve(),
                         self.code / "dsh-plugins/dsh-bio-agent")
        before = (profile / "package.json").stat().st_mtime_ns
        provision.enable_web_profile("alice")
        self.assertEqual((profile / "package.json").stat().st_mtime_ns, before)

    def test_profile_and_preset_file_links_fail_without_changing_target(self):
        for index, relative in enumerate((
            "profiles/web/package.json", "profiles/web/cordis.yml",
            ".agent-presets/md-sim-assistant/agent.cordis.yml",
            ".agent-presets/md-sim-assistant/preset.yml",
        )):
            with self.subTest(relative=relative):
                self.new_tenant("file-link-" + str(index))
                destination = self.home / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(self.sentinel)
                with self.assertRaises((OSError, ValueError)):
                    provision.enable_web_profile("alice")
                self.assert_sentinel_unchanged()

    def test_profile_ancestors_and_module_directory_links_are_rejected(self):
        for index, relative in enumerate((
            "profiles", "profiles/web", "profiles/node_modules", ".agent-presets",
        )):
            with self.subTest(relative=relative):
                self.new_tenant("directory-link-" + str(index))
                destination = self.home / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(self.outside, target_is_directory=True)
                with self.assertRaises((OSError, ValueError)):
                    provision.enable_web_profile("alice")
                self.assert_sentinel_unchanged()
                self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["sentinel.json"])

    def test_hardlinked_package_is_rejected(self):
        profile = self.home / "profiles/web"
        profile.mkdir(parents=True)
        os.link(self.sentinel, profile / "package.json")
        with self.assertRaises((OSError, ValueError)):
            provision.enable_web_profile("alice")
        self.assert_sentinel_unchanged()

    def test_final_name_swap_cannot_redirect_atomic_publish(self):
        original = os.replace
        profile = self.home / "profiles/web"
        def swap(source, destination, **kwargs):
            if destination == "package.json":
                target = profile / destination
                if target.exists():
                    target.unlink()
                target.symlink_to(self.sentinel)
            return original(source, destination, **kwargs)
        with patch.object(tenant_files.os, "replace", side_effect=swap):
            provision.enable_web_profile("alice")
        self.assert_sentinel_unchanged()
        self.assertFalse((profile / "package.json").is_symlink())
        self.assertIn("dsh", json.loads((profile / "package.json").read_text()))

    def test_new_directories_and_files_are_owned_through_descriptors(self):
        from types import SimpleNamespace
        owner = (os.geteuid(), os.getegid())
        with patch.object(provision, "_skip_unix", return_value=False), \
             patch.object(provision.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=owner[0])), \
             patch.object(provision.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=owner[1])), \
             patch.object(provision, "_chown_tree", side_effect=AssertionError("PATH_WALK_FORBIDDEN")), \
             patch.object(tenant_files.os, "chown", side_effect=AssertionError("PATH_CHOWN_FORBIDDEN")):
            provision.enable_web_profile("alice")
        for relative in ("profiles", "profiles/web", ".agent-presets/md-sim-assistant"):
            actual = (self.home / relative).stat()
            self.assertEqual((actual.st_uid, actual.st_gid), owner)
        file = (self.home / "profiles/web/package.json").stat()
        self.assertEqual((file.st_uid, file.st_gid), owner)
        self.assertEqual(file.st_mode & 0o777, 0o600)

    def test_directory_ownership_does_not_transfer_shared_ancestors(self):
        destination = self.base / "new-shared/users/alice/home"
        changed = []
        def record(descriptor, uid, gid):
            changed.append(os.fstat(descriptor).st_ino)
        with patch.object(tenant_files.os, "fchown", side_effect=record):
            with tenant_files.home_directory(destination, create=True,
                                             owner=(os.geteuid(), os.getegid())):
                pass
        self.assertEqual(changed, [destination.stat().st_ino])


if __name__ == "__main__":
    unittest.main()

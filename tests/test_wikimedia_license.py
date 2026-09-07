from __future__ import annotations

from aimz.providers.assets.wikimedia import license_allowed

ALLOWED = ["pd", "cc0", "cc-by", "cc-by-sa", "public domain"]


def test_allowed_licenses() -> None:
    for lic in ["Public domain", "PD-US", "CC0", "CC BY 4.0", "CC BY-SA 3.0", "CC BY-SA 4.0"]:
        assert license_allowed(lic, ALLOWED), lic


def test_disallowed_licenses() -> None:
    for lic in ["CC BY-NC 4.0", "CC BY-ND 2.0", "CC BY-NC-SA 3.0", "Fair use", "Copyrighted", "GFDL", ""]:
        assert not license_allowed(lic, ALLOWED), lic

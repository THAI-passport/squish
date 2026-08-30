"""Static contract tests for the responsive grouped tool picker.

The picker is intentionally rendered from the same registry as the desktop
sidebar. These checks guard the responsive and accessibility wiring without
introducing a frontend build or DOM-test dependency into Squish.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "backend/static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "backend/app.py").read_text(encoding="utf-8")
CLIENT = (ROOT / "backend/static/client_tools.js").read_text(encoding="utf-8")


def test_mobile_picker_has_disclosure_contract():
    assert 'id="mobileToolTrigger"' in INDEX
    assert 'aria-expanded="false"' in INDEX
    assert 'aria-controls="groups"' in INDEX
    assert "function setMobileToolMenu" in INDEX
    assert "function setMobileOpenGroup" in INDEX
    assert "section.querySelector('.tool-group-toggle')?.setAttribute('aria-expanded'" in INDEX


def test_registry_groups_drive_both_desktop_and_mobile_navigation():
    assert "function appendToolGroup(box, name, items)" in INDEX
    assert "appendToolGroup(box, 'Pinned', pinnedItems)" in INDEX
    assert "appendToolGroup(box, g, items)" in INDEX
    assert "for(const t of items) itemBox.append(makeToolButton(t))" in INDEX


def test_mobile_picker_is_compact_and_touch_safe():
    assert ".sidebar.is-picker-open .tool-groups { display:block; }" in INDEX
    assert "html.mobile-tool-menu-open, body.mobile-tool-menu-open { overflow-y:hidden; }" in INDEX
    assert "document.body.classList.toggle('mobile-tool-menu-open', mobileToolMenuOpen)" in INDEX
    assert ".tool-group.is-open .tool-group-items" in INDEX
    assert ".tool-item .tool-grip, .tool-item .tool-pin { display:none; }" in INDEX
    assert "max-height:min(58dvh,34rem)" in INDEX


def test_mobile_picker_closes_on_selection_and_escape():
    open_body = INDEX.split("function open_(key){", 1)[1].split("function home(){", 1)[0]
    assert "setMobileToolMenu(false)" in open_body
    assert "mobileOpenGroup = current.group" in open_body
    assert "setMobileToolMenu(false);\n    render();" in open_body
    assert "if (mobileToolMenuOpen) { setMobileToolMenu(false, true); }" in INDEX


def test_static_and_server_versions_stay_aligned():
    app_version = re.search(r'APP_VERSION = "([^"]+)"', APP).group(1)
    static_version = re.search(r'const STATIC_VERSION = "([^"]+)"', CLIENT).group(1)
    assert app_version == static_version
    assert f"/vault.js?v={app_version}" in INDEX
    assert f"client_tools.js?v={app_version}" in INDEX

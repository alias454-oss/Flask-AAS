# tests/test_admin_ui_contract.py

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_TEMPLATES = ROOT / "app" / "templates" / "admin"
DEFAULT_THEME = ROOT / "app" / "templates" / "themes" / "default"
STATIC_JS = ROOT / "app" / "static" / "js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_host_admin_menu_is_shared_by_default_and_admin_sidebars():
    menu = _read(ADMIN_TEMPLATES / "includes" / "menu.html")
    default_sidebar = _read(DEFAULT_THEME / "sidebar.html")
    admin_sidebar = _read(ADMIN_TEMPLATES / "includes" / "sidebar.html")

    assert "<nav" not in menu
    assert '<h3>Admin Menu</h3>' in menu
    assert ">Admin Home</a>" in menu
    assert ">Site Settings</a>" in menu
    assert ">Applications</a>" in menu
    assert ">Register User</a>" in menu
    assert ">View Users</a>" in menu

    include = '{% include "admin/includes/menu.html" %}'
    assert include in default_sidebar
    assert include in admin_sidebar
    assert "quick_stats.total_users" in admin_sidebar
    assert "quick_stats.total_users" not in default_sidebar
    assert '<nav class="sidebar-section"' not in default_sidebar
    assert '<nav class="sidebar-section"' not in admin_sidebar


def test_admin_shell_uses_shared_theme_contract_without_losing_admin_hooks():
    base = _read(ADMIN_TEMPLATES / "includes" / "base.html")
    header = _read(ADMIN_TEMPLATES / "includes" / "header.html")
    footer = _read(ADMIN_TEMPLATES / "includes" / "footer.html")

    assert "themes/default/style.css" in base
    assert 'class="site-body"' in base
    assert 'class="skip-link" href="#main-content"' in base
    assert 'id="main-content" class="site-main"' in base
    assert 'id="site-sidebar" class="sidebar"' in base
    assert "{% block extra_styles %}" in base

    # Admin-specific extension points remain intentionally separate.
    assert "{% if enable_analytics %}" in base
    assert "{% block scripts %}" in base
    assert "admin/includes/sidebar.html" in base
    assert "plugin_navigation()" not in header

    assert 'id="site-header" class="site-header"' in header
    assert 'id="primary-navigation" class="menu"' in header
    assert 'aria-label="Primary navigation"' in header
    assert 'id="site-footer" class="site-footer"' in footer


def test_admin_scripts_are_first_party_and_avoid_dynamic_code_execution():
    list_users = _read(ADMIN_TEMPLATES / "list_users.html")
    edit_user = _read(ADMIN_TEMPLATES / "edit_user.html")

    assert "js/admin_users.js" in list_users
    assert "js/location_fields.js" in edit_user
    assert "http://" not in list_users
    assert "https://" not in list_users
    assert "http://" not in edit_user
    assert "https://" not in edit_user
    assert 'name="csrf_token"' in list_users

    forbidden = (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    )
    for name in ("admin_users.js", "location_fields.js"):
        source = _read(STATIC_JS / name)
        for token in forbidden:
            assert token not in source

    admin_users = _read(STATIC_JS / "admin_users.js")
    assert "window.confirm" in admin_users
    assert ".js-remove-profile-image-form" in admin_users
    assert "Remove this user's profile image?" in admin_users
    assert 'form.addEventListener("submit"' in admin_users

    locations = _read(STATIC_JS / "location_fields.js")
    assert "url.origin !== window.location.origin" in locations
    assert 'credentials: "same-origin"' in locations
    assert 'headers: {"Accept": "application/json"}' in locations


def test_admin_templates_do_not_import_remote_javascript():
    script_src = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)

    for template in ADMIN_TEMPLATES.rglob("*.html"):
        source = _read(template)
        for src in script_src.findall(source):
            assert not src.startswith(("http://", "https://", "//"))


def test_admin_home_uses_real_quick_stats_and_clear_admin_destinations():
    template = _read(ADMIN_TEMPLATES / "admin.html")
    css = _read(ROOT / "app" / "static" / "themes" / "default" / "style.css")
    base = _read(ADMIN_TEMPLATES / "includes" / "base.html")

    assert '<div class="admin-dashboard">' in template
    assert '<div class="admin-stat-grid">' in template
    assert "quick_stats.total_users" in template
    assert "quick_stats.pending_users" in template
    assert "quick_stats.visitor_tracking_enabled" in template
    assert "quick_stats.online_users" in template
    assert "quick_stats.online_guests" in template
    assert '>View Users</a>' in template
    assert '>Register User</a>' not in template
    assert '>Open Settings</a>' in template
    assert '>Manage Applications</a>' in template

    assert ".admin-stat-grid {" in css
    assert ".admin-tool-grid {" in css
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in css
    assert '<!-- PageGen in {{ page_gen_time }} ms -->' not in base

def test_list_users_consumes_internal_profile_image_data_without_media_route():
    template = _read(ADMIN_TEMPLATES / "list_users.html")
    route = _read(ROOT / "app" / "routes" / "admin" / "users.py")
    account_route = _read(ROOT / "app" / "routes" / "account" / "account.py")

    assert "profile_images.get(user.id)" in template
    assert "filename=user.image" not in template
    assert "profile_image_data_uri(user.image)" in route
    assert "delete_profile_image(profile_image_filename)" in route
    assert "url_for('users.remove_profile_image', user_id=user.id)" in template
    assert '{% if user.image %}' in template
    assert 'class="inline-action-form js-remove-profile-image-form"' in template
    assert 'action="admin_profile_image_removed"' in route
    assert "delete_profile_image(old_filename)" in route
    assert "send_file" not in account_route
    assert "@account_bp.route('/account/profile-image/<" not in account_route

def test_user_storage_path_contract_is_explicit_and_not_host_extended():
    settings_template = _read(ADMIN_TEMPLATES / "settings.html")
    seeder = _read(ROOT / "app" / "core" / "seeder.py")
    avatar = _read(ROOT / "app" / "core" / "avatar.py")

    assert '"users_stored_path": "static/images/users"' in seeder
    assert "Relative paths resolve from the application root." in settings_template
    assert "Absolute paths are used as configured." in settings_template
    assert "Changing this path does not move existing user files." in settings_template
    assert 'Path(current_app.root_path) / root' in avatar
    assert 'getattr(env, "users_stored_path"' in avatar
    assert "USER_IMAGE_ROOT" not in avatar

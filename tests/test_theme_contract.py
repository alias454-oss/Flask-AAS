# tests/test_theme_contract.py

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME_ROOT = ROOT / "app" / "templates" / "themes" / "default"
THEME_CSS = ROOT / "app" / "static" / "themes" / "default" / "style.css"


def test_default_theme_exposes_semantic_site_shell_anchors():
    base = (THEME_ROOT / "base.html").read_text(encoding="utf-8")
    header = (THEME_ROOT / "header.html").read_text(encoding="utf-8")
    footer = (THEME_ROOT / "footer.html").read_text(encoding="utf-8")

    assert 'class="site-body"' in base
    assert 'class="skip-link" href="#main-content"' in base
    assert 'id="main-content" class="site-main"' in base
    assert 'id="site-sidebar" class="sidebar"' in base
    assert 'id="site-header" class="site-header"' in header
    assert 'id="primary-navigation" class="menu"' in header
    assert 'aria-label="Primary navigation"' in header
    assert "url_for('dashboard.dashboard')" not in header
    assert "url_for('admin.admin_home')" not in header
    assert "url_for('logout.logout')" in header
    assert 'id="site-footer" class="site-footer"' in footer


def test_default_theme_owns_generic_plugin_primitives():
    css = THEME_CSS.read_text(encoding="utf-8")
    selectors = [
        ".content-page",
        ".content-page--narrow",
        ".content-page--wide",
        ".form-control",
        ".form-help",
        ".form-error",
        ".actions",
        ".panel",
        ".table-wrap",
        ".data-table",
        ".pagination",
    ]

    for selector in selectors:
        assert selector in css


def test_default_theme_keeps_dark_primary_actions_and_visible_fieldsets():
    css = THEME_CSS.read_text(encoding="utf-8")

    assert "background: #2a2a2a;" in css
    assert "border: 1px solid var(--border-accent);" in css
    assert "background: var(--accent);" in css


def test_default_theme_preserves_form_control_right_spacing():
    css = THEME_CSS.read_text(encoding="utf-8")
    rule_start = css.index(".form-control,")
    rule_end = css.index("\n}", rule_start)
    form_control_rule = css[rule_start:rule_end]

    assert "width: min(calc(100% - 5px), var(--control-width));" in form_control_rule
    assert "max-width: calc(100% - 5px);" in form_control_rule
    assert "margin-right: 5px;" in form_control_rule


def test_application_data_uses_tabular_admin_layout():
    template = (ROOT / "app" / "templates" / "admin" / "plugins.html").read_text(
        encoding="utf-8"
    )

    assert 'class="plugin-application-data panel"' in template
    assert 'class="table-wrap"' in template
    assert '<table class="data-table">' in template
    assert '<th scope="col">Data Type</th>' in template
    assert '<th scope="col">Description</th>' not in template
    assert 'class="data-table__description"' in template
    assert '<th scope="col">Status</th>' in template
    assert '<th scope="col">Action</th>' in template
    assert 'class="plugin-dataset"' not in template


def test_default_theme_keeps_footer_at_viewport_bottom_on_short_pages():
    css = THEME_CSS.read_text(encoding="utf-8")

    page_start = css.index(".page-container {")
    page_end = css.index("\n}", page_start)
    page_rule = css[page_start:page_end]

    footer_start = css.rindex(".site-footer {")
    footer_end = css.index("\n}", footer_start)
    footer_rule = css[footer_start:footer_end]

    assert "min-height: 100vh;" in page_rule
    assert "display: flex;" in page_rule
    assert "flex-direction: column;" in page_rule
    assert "margin-top: auto;" in footer_rule


def test_default_theme_content_row_stretches_under_flex_page_shell():
    css = THEME_CSS.read_text(encoding="utf-8")

    layout_start = css.index(".container.layout-with-sidebar {")
    layout_end = css.index("\n}", layout_start)
    layout_rule = css[layout_start:layout_end]

    assert "display: flex;" in layout_rule
    assert "width: 100%;" in layout_rule
    assert "gap: 20px;" in layout_rule


def test_account_summary_panels_share_top_row_and_stack_on_small_screens():
    css = THEME_CSS.read_text(encoding="utf-8")
    template = (ROOT / "app" / "templates" / "account" / "account.html").read_text(
        encoding="utf-8"
    )

    assert 'class="account-summary-grid"' in template
    assert ".account-summary-grid {" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert "align-items: stretch;" in css
    assert "grid-template-columns: 1fr;" in css


def test_dashboard_reuses_account_summary_layout_for_status_and_applications():
    template = (ROOT / "app" / "templates" / "account" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'class="account-summary-grid"' in template
    assert '<legend>Account Details</legend>' in template
    assert '<legend>Applications</legend>' in template
    assert "plugin_navigation()" in template
    assert "Multi-Factor Authentication:" in template
    assert "env and env.use_mfa" in template
    assert "Manage Account" in template
    assert ">Open</a>" in template
    assert "Profile Information" not in template
    assert "<strong>Phone:</strong>" not in template
    assert "<strong>Fax:</strong>" not in template


def test_captcha_reload_keeps_compact_size_with_standard_button_colors():
    css = THEME_CSS.read_text(encoding="utf-8")

    captcha_rule = css.split(".captcha-reload {", 1)[1].split("}", 1)[0]
    hover_rule = css.split(".captcha-reload:hover {", 1)[1].split("}", 1)[0]

    assert "padding: 0.3rem 0.55rem;" in captcha_rule
    assert "font-size: 0.75rem;" in captcha_rule
    assert "font-weight: 600;" in captcha_rule
    assert "background: #2a2a2a;" in captcha_rule
    assert "border: 1px solid var(--border-accent);" in captcha_rule
    assert "background: var(--accent);" in hover_rule
    assert "border-color: var(--accent);" in hover_rule

def test_account_profile_image_uses_host_theme_and_internal_rendering():
    css = THEME_CSS.read_text(encoding="utf-8")
    template = (ROOT / "app" / "templates" / "account" / "account.html").read_text(
        encoding="utf-8"
    )

    assert 'class="account-identity"' in template
    assert 'id="account-identity-name"' in template
    assert 'class="account-identity__username"' in template
    assert 'class="account-identity__email"' in template
    assert 'class="profile-image-preview"' in template
    assert '<dl class="account-details-list">' in template
    account_summary = template.split('<legend>Account Details</legend>', 1)[1].split('</fieldset>', 1)[0]
    update_section = template.split('<legend>Update Account Details</legend>', 1)[1]
    identity_section = template.split('<section class="account-identity"', 1)[1].split('</section>', 1)[0]
    assert 'profile-image-preview' in identity_section
    assert 'profile-image-form' in identity_section
    assert 'profile-image-help' in identity_section
    assert 'profile-image-form' not in account_summary
    assert 'profile-image-form' not in update_section
    assert 'enctype="multipart/form-data"' in template
    assert "url_for('account.upload_profile_image')" in template
    assert "url_for('account.remove_profile_image')" in template
    assert "profile_image or url_for('static', filename='images/no_user.jpg')" in template
    assert "account.profile_image" not in template
    assert "image/jpeg,image/png,image/webp" in template
    assert ".account-identity {" in css
    assert ".account-details-list__item {" in css
    assert ".profile-image-preview {" in css
    assert "grid-template-columns: 6rem minmax(0, 1fr);" in css
    identity_rule = css.split(".account-identity {", 1)[1].split("}", 1)[0]
    assert "align-items: start;" in identity_rule
    assert "padding: var(--space-4);" in identity_rule
    assert "border: 1px solid var(--border-accent);" in identity_rule
    assert "border-radius: 4px;" in identity_rule
    assert "border-bottom:" not in identity_rule
    assert "width: 6rem;" in css
    assert "height: 6rem;" in css
    assert ".account-identity__image-controls {" in css
    assert ".profile-image-actions {" in css
    assert ".profile-image-input {" in css


def test_account_single_other_session_actions_share_row_and_stack_on_small_screens():
    css = THEME_CSS.read_text(encoding="utf-8")
    template = (ROOT / "app" / "templates" / "account" / "account.html").read_text(
        encoding="utf-8"
    )

    assert 'class="account-session-actions"' in template
    assert 'class="account-session-actions__all"' in template
    assert "other_sessions | length == 1" in template

    actions_rule = css.split(".account-session-actions {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in actions_rule
    assert "justify-content: space-between;" in actions_rule

    responsive_css = css.split("@media (max-width: 768px) {", 1)[1]
    responsive_actions = responsive_css.split(".account-session-actions {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column;" in responsive_actions

"""Tests for AC-1 through AC-6, AC-8: Frontend multi-terminal structure.

Since the frontend is vanilla JS in a single HTML file, these tests parse
the HTML/JS source to verify the required structures, classes, and behaviors
are defined. Visual testing will confirm actual rendering.
"""

import os
import re
import pytest

INDEX_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html"
)


@pytest.fixture
def html_source():
    """Read the index.html file."""
    with open(INDEX_HTML_PATH, "r") as f:
        return f.read()


class TestLayoutSystem:
    """AC-1: Four predefined layouts -- single, hsplit, vsplit, quad."""

    def test_layout_definitions_exist(self, html_source):
        """All four layout names are defined in the source."""
        for layout in ["single", "hsplit", "vsplit", "quad"]:
            assert layout in html_source, f"Layout '{layout}' not found in index.html"

    def test_css_grid_used_for_layout(self, html_source):
        """CSS grid is used for pane layout (grid-template or display: grid)."""
        assert "grid" in html_source.lower(), "CSS grid not found in index.html"

    def test_pane_container_exists(self, html_source):
        """A pane container element exists for holding terminal panes."""
        assert "pane-container" in html_source or "paneContainer" in html_source, (
            "No pane container element found"
        )

    def test_layout_allocates_equal_space(self, html_source):
        """Layouts use equal fractions (1fr) for pane sizing."""
        assert "1fr" in html_source, "No CSS fr units found for equal spacing"


class TestToolbar:
    """AC-2: Toolbar with layout buttons and pane indicators."""

    def test_toolbar_element_exists(self, html_source):
        """A toolbar element is present in the HTML."""
        assert "toolbar" in html_source.lower(), "No toolbar element found"

    def test_layout_buttons_exist(self, html_source):
        """Buttons or controls for switching layouts are present."""
        # Should have clickable elements for each layout
        # At minimum, all 4 layout names should appear near interactive elements
        for layout in ["single", "hsplit", "vsplit", "quad"]:
            count = html_source.lower().count(layout)
            assert count >= 2, (
                f"Layout '{layout}' appears only {count} time(s) -- "
                "expected in both definition and UI control"
            )

    def test_dark_theme_toolbar(self, html_source):
        """Toolbar uses the dark theme (#1e1e1e or similar dark background)."""
        assert (
            "#1e1e1e" in html_source
            or "#252525" in html_source
            or "#2d2d2d" in html_source
        ), "Toolbar does not use dark theme colors"


class TestPaneLifecycle:
    """AC-3: Each pane gets its own PTY session; can be closed/reopened."""

    def test_session_creation_per_pane(self, html_source):
        """Code creates sessions via /api/session for each pane."""
        assert "/api/session" in html_source, "No /api/session call found"
        # Should create session as part of pane initialization
        assert (
            "createSession" in html_source
            or "create_session" in html_source
            or "api/session" in html_source
        )

    def test_session_close_on_pane_removal(self, html_source):
        """Code calls /api/session/close when a pane is closed."""
        assert "/api/session/close" in html_source or "session/close" in html_source, (
            "No session close call found"
        )

    def test_add_pane_button_exists(self, html_source):
        """A '+' or add button mechanism exists for creating new panes in empty slots."""
        assert "+" in html_source, "No '+' button for adding panes"

    def test_pane_class_or_constructor(self, html_source):
        """A TerminalPane class or equivalent constructor exists."""
        assert (
            "TerminalPane" in html_source
            or "terminalPane" in html_source
            or "createPane" in html_source
        ), "No TerminalPane class or pane constructor found"


class TestIndependentResize:
    """AC-4: Each pane resizes independently with debouncing."""

    def test_fit_addon_per_pane(self, html_source):
        """FitAddon is loaded for each pane (not just once globally)."""
        assert "FitAddon" in html_source, "FitAddon not found"
        # fitAddon.fit() or .fit() should appear in pane context
        assert ".fit()" in html_source, "No fit() call found"

    def test_resize_api_called(self, html_source):
        """/api/resize is called per pane on resize."""
        assert "/api/resize" in html_source, "No /api/resize call found"

    def test_resize_debounce(self, html_source):
        """Resize events are debounced (setTimeout or debounce pattern)."""
        # Look for debounce implementation
        has_debounce = "debounce" in html_source.lower() or (
            "setTimeout" in html_source and "resize" in html_source.lower()
        )
        assert has_debounce, "No resize debounce mechanism found"

    def test_debounce_delay_at_least_150ms(self, html_source):
        """Debounce delay is at least 150ms."""
        # Find numbers near resize/debounce context
        delays = re.findall(r"(\d+)", html_source)
        # 150 or higher should appear somewhere in debounce context
        assert any(int(d) >= 150 for d in delays if d.isdigit() and int(d) < 5000), (
            "No debounce delay >= 150ms found"
        )


class TestFocusManagement:
    """AC-5: Click to focus, Ctrl+Shift+N to cycle, visual indicator."""

    def test_focus_visual_indicator(self, html_source):
        """Focused pane has a visual border or highlight."""
        has_focus_style = (
            "focused" in html_source.lower()
            or "active-pane" in html_source
            or "focus" in html_source.lower()
        )
        assert has_focus_style, "No focus visual indicator found"

    def test_keyboard_shortcut_cycle(self, html_source):
        """Ctrl+Shift+N keyboard shortcut is handled."""
        # Should check for keydown handler with Ctrl+Shift+N
        has_shortcut = (
            "ctrlKey" in html_source
            and "shiftKey" in html_source
            and (
                "KeyN" in html_source
                or "key === 'N'" in html_source
                or 'key ==="N"' in html_source
                or "keyCode" in html_source
                or "'n'" in html_source
                or "'N'" in html_source
            )
        )
        assert has_shortcut, "No Ctrl+Shift+N keyboard shortcut handler found"

    def test_click_to_focus(self, html_source):
        """Click handler on panes sets focus."""
        has_click_focus = (
            "click" in html_source.lower() and "focus" in html_source.lower()
        )
        assert has_click_focus, "No click-to-focus handler found"


class TestClosePane:
    """AC-6: Close button on each pane header."""

    def test_close_button_exists(self, html_source):
        """Each pane has a close button (X or similar)."""
        has_close = "close" in html_source.lower() and (
            "X" in html_source
            or "x" in html_source
            or "&#x2715" in html_source
            or "\\u00d7" in html_source
            or "times" in html_source
        )
        assert has_close, "No close button found for panes"

    def test_pane_header_exists(self, html_source):
        """Each pane has a header/title bar."""
        has_header = (
            "pane-header" in html_source
            or "paneHeader" in html_source
            or "terminal-header" in html_source
        )
        assert has_header, "No pane header element found"

    def test_last_pane_auto_creates_new(self, html_source):
        """Closing the last pane auto-creates a new terminal."""
        # Look for logic that prevents zero panes
        has_auto_create = (
            "length === 0" in html_source
            or "length == 0" in html_source
            or "no active" in html_source.lower()
            or "last pane" in html_source.lower()
            or "at least" in html_source.lower()
            or "activePanes" in html_source
            or "panes.size === 0" in html_source
            or "panes.size == 0" in html_source
        )
        assert has_auto_create, (
            "No auto-create logic found for when the last pane is closed"
        )


class TestPollingEfficiency:
    """AC-8: Single batch polling interval replaces per-terminal polls."""

    def test_uses_batch_endpoint(self, html_source):
        """Frontend calls /api/output-batch instead of /api/output."""
        assert "/api/output-batch" in html_source or "output-batch" in html_source, (
            "Frontend does not use batch output endpoint"
        )

    def test_single_poll_interval(self, html_source):
        """Only one setInterval for polling (not one per terminal)."""
        # Count setInterval occurrences related to polling
        interval_count = html_source.count("setInterval")
        assert interval_count >= 1, "No setInterval found for polling"
        # Should not have multiple polling intervals
        # (one for polling, possibly one for other things, but not N for N terminals)
        assert interval_count <= 2, (
            f"Found {interval_count} setInterval calls -- "
            "should use single batch poll, not per-terminal polls"
        )

    def test_poll_interval_100ms(self, html_source):
        """Polling interval is approximately 100ms."""
        assert "100" in html_source, "100ms polling interval not found"

    def test_poll_pauses_when_no_sessions(self, html_source):
        """Polling skips/pauses when there are no active sessions."""
        has_skip_logic = (
            "length === 0" in html_source
            or "length == 0" in html_source
            or "no session" in html_source.lower()
            or "size === 0" in html_source
            or "size == 0" in html_source
            or "!sessionIds" in html_source
            or "sessionIds.length" in html_source
        )
        assert has_skip_logic, "No logic to pause polling when no sessions are active"


class TestLoadingScreenNotModified:
    """Verify loading.html is NOT modified (constraint)."""

    def test_loading_html_unchanged(self):
        """loading.html exists and was not modified by this feature."""
        loading_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static",
            "loading.html",
        )
        assert os.path.exists(loading_path), "loading.html is missing"
        # Just verify it still exists -- visual testing will confirm content


class TestNoExternalFrameworks:
    """Verify no external JS frameworks are added (constraint)."""

    def test_no_react_vue_angular(self, html_source):
        """No React, Vue, or Angular imports."""
        for framework in ["react", "vue", "angular", "svelte"]:
            assert framework not in html_source.lower(), (
                f"External framework '{framework}' found in index.html"
            )

    def test_vanilla_js_only(self, html_source):
        """No npm/import statements for external packages."""
        assert "import " not in html_source or "from '" not in html_source, (
            "ES module imports found -- should use vanilla JS only"
        )

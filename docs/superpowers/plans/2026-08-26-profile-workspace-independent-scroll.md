# Profile Workspace Independent Scroll Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the member directory and profile detail independent desktop scroll areas while retaining the existing stacked mobile layout.

**Architecture:** Constrain the existing master/detail browser to the viewport space available below the plugin shell, then allow each grid child to shrink and scroll independently. Keep scroll state local to the two existing DOM nodes; selecting a member resets only the detail pane before loading the new profile.

**Tech Stack:** Vanilla JavaScript, CSS Grid/Flexbox, pytest source-contract tests.

---

### Task 1: Independent master/detail scrolling

**Files:**
- Modify: `tests/page/test_profile_workspace.py`
- Modify: `pages/settings/styles/components.css`
- Modify: `pages/settings/workspaces/profiles.js`

- [ ] **Step 1: Write the failing layout contract test**

Add a test that requires a viewport-bounded profile browser, shrinkable grid children, independent vertical scrolling for the detail host, mobile overflow reset, and a detail scroll reset in the member click handler:

```python
def test_profile_workspace_scrolls_directory_and_detail_independently():
    source = (PAGE / "workspaces" / "profiles.js").read_text(encoding="utf-8")
    styles = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")

    assert "height: max(35rem, calc(100dvh - 15.875rem));" in styles
    assert ".profile-directory" in styles and "min-height: 0" in styles
    assert ".profile-detail-host" in styles and "overflow-y: auto" in styles
    assert "detailHost.scrollTop = 0" in source
    mobile = styles.split("@media (max-width: 44rem)", 1)[1]
    assert ".profile-browser { display: block; height: auto;" in mobile
    assert ".profile-detail-host { overflow: visible; }" in mobile
```

- [ ] **Step 2: Run the test and verify it fails for the current root cause**

Run: `pytest -q tests/page/test_profile_workspace.py -k independently`

Expected: FAIL because `.profile-browser` has only `min-height`, `.profile-detail-host` has no overflow, and member selection does not reset `scrollTop`.

- [ ] **Step 3: Add the minimum desktop and mobile CSS**

Update the existing profile rules without changing colors or component structure:

```css
.profile-workspace { min-width: 0; min-height: 0; }
.profile-browser {
  height: max(35rem, calc(100dvh - 15.875rem));
  min-height: 35rem;
}
.profile-directory,
.profile-detail-host { min-height: 0; }
.profile-detail-host { overflow-y: auto; overscroll-behavior: contain; }

@media (max-width: 44rem) {
  .profile-browser { display: block; height: auto; min-height: 0; overflow: visible; }
  .profile-detail-host { overflow: visible; }
}
```

The existing flex rule on `.profile-member-list` remains the left-pane scroller, so the header and search field stay outside it.

- [ ] **Step 4: Reset only the detail pane when selecting a member**

At the beginning of the member row click handler, before replacing the detail content, add:

```javascript
detailHost.scrollTop = 0;
```

Do not scroll the page or change `.profile-member-list.scrollTop`.

- [ ] **Step 5: Run the focused profile tests**

Run: `pytest -q tests/page/test_profile_workspace.py`

Expected: all tests PASS.

- [ ] **Step 6: Check CSS/JS diff hygiene and commit**

Run: `git diff --check`

Expected: no output.

```bash
git add tests/page/test_profile_workspace.py pages/settings/styles/components.css pages/settings/workspaces/profiles.js
git commit -m "fix: scroll profile panes independently"
```

### Task 2: Focused regression verification

**Files:**
- Modify only if a focused regression exposes a defect in the planned files.

- [ ] **Step 1: Run adjacent page contracts**

Run: `pytest -q tests/page/test_profile_workspace.py tests/page/test_runtime_layout.py tests/page/test_accessibility_contract.py`

Expected: all tests PASS.

- [ ] **Step 2: Confirm repository state**

Run: `git status --short && git diff --check`

Expected: only the pre-existing user-owned `analysis/` directory remains untracked and diff check prints nothing.

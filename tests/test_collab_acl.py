"""ACL for hybrid collab org roles (viewer is read-only)."""

from __future__ import annotations

import unittest

from navin.collab.acl import (
    action_allowed,
    can_approve,
    can_execute_tools,
    is_read_only,
    normalize_org_role,
    role_from_license,
)
from navin.config.schema import Config


class NormalizeRoleTest(unittest.TestCase):
    def test_known_roles(self):
        for role in ("admin", "member", "viewer", "Admin", " VIEWER "):
            self.assertIsNotNone(normalize_org_role(role))

    def test_unknown_is_none(self):
        self.assertIsNone(normalize_org_role("owner"))
        self.assertIsNone(normalize_org_role(""))
        self.assertIsNone(normalize_org_role(None))
        self.assertIsNone(normalize_org_role(12))


class ToolExecAclTest(unittest.TestCase):
    def test_empty_role_keeps_full_access(self):
        self.assertTrue(can_execute_tools(None))
        self.assertTrue(can_execute_tools(""))
        self.assertTrue(can_approve(None))
        self.assertFalse(is_read_only(None))

    def test_admin_and_member_can_run(self):
        for role in ("admin", "member"):
            self.assertTrue(can_execute_tools(role))
            self.assertTrue(can_approve(role))
            self.assertFalse(is_read_only(role))

    def test_viewer_is_blocked(self):
        self.assertFalse(can_execute_tools("viewer"))
        self.assertFalse(can_approve("viewer"))
        self.assertTrue(is_read_only("viewer"))
        self.assertFalse(action_allowed("viewer", "message"))
        self.assertFalse(action_allowed("viewer", "approval_decision"))
        self.assertFalse(action_allowed("viewer", "choice_answer"))
        self.assertFalse(action_allowed("viewer", "terminal_open"))
        self.assertFalse(action_allowed("viewer", "mobile_preview_open"))
        self.assertFalse(action_allowed("viewer", "mobile_preview_input"))
        self.assertTrue(action_allowed("viewer", "attach"))
        self.assertTrue(action_allowed("viewer", "presence_sync"))
        self.assertTrue(action_allowed("admin", "mobile_preview_input"))


class LicenseRoleTest(unittest.TestCase):
    def test_reads_org_role_from_config(self):
        config = Config()
        config.license.org_role = "viewer"
        self.assertEqual(role_from_license(config), "viewer")
        self.assertEqual(role_from_license(config.license), "viewer")


if __name__ == "__main__":
    unittest.main()

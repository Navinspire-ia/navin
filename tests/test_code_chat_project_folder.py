# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A Code chat started without a project works in a folder of its own."""

from navin.session.manager import SessionManager
from navin.webui.workspaces import WebUIWorkspaceController, project_folder_name


def test_folder_name_keeps_what_the_project_is():
    assert project_folder_name("crée moi une app de todo list en react") == "todo-list"
    assert project_folder_name("Fais-moi un jeu snake en Python stp") == "jeu-snake"
    assert project_folder_name("build a todo app in react") == "todo-react"
    assert project_folder_name("Crée un site vitrine pour une boulangerie") == "vitrine-boulangerie"
    assert project_folder_name("une app") == "app"
    assert project_folder_name("اصنع لي تطبيق") == "projet"


def test_first_code_message_moves_the_chat_into_a_new_project_folder(tmp_path):
    root = tmp_path / "NavinProjects"
    root.mkdir()
    (root / "jeu-snake").mkdir()
    sessions = SessionManager(tmp_path / "ws")
    controller = WebUIWorkspaceController(
        session_manager=sessions, default_workspace=root, default_restrict_to_workspace=False,
    )
    scope = controller.default_scope()

    project = controller.project_for_code_chat("c1", scope, "Fais-moi un jeu snake en Python")
    assert project is not None
    assert project.project_path == (root / "jeu-snake-2").resolve()
    assert project.project_path.is_dir()
    assert project.access_mode == scope.access_mode

    # A chat already in a project, or already started, stays where it is.
    assert controller.project_for_code_chat("c1", project, "encore") is None
    session = sessions.get_or_create("websocket:c2")
    session.add_message("user", "salut")
    assert controller.project_for_code_chat("c2", scope, "une app") is None


def test_request_phrasing_is_not_the_folder_name():
    assert project_folder_name("The user wants to develop a 3D application for knight races") == "3d-knight"


def test_default_workspace_that_is_a_git_repo_gets_no_sub_folder(tmp_path):
    root = tmp_path / "financeIa"
    (root / ".git").mkdir(parents=True)
    controller = WebUIWorkspaceController(
        session_manager=SessionManager(tmp_path / "ws"),
        default_workspace=root,
        default_restrict_to_workspace=False,
    )
    scope = controller.default_scope()
    assert controller.project_for_code_chat("c1", scope, "The user wants a game") is None
    assert [p.name for p in root.iterdir()] == [".git"]

"""cml_tools のユニットテスト。実CMLは不要（すべてモック）。"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# ヘルパー: virl2_client のモック構造を組み立てる
# ---------------------------------------------------------------------------


def _make_mock_node(label: str = "R1", state: str = "BOOTED") -> MagicMock:
    node = MagicMock()
    node.id = f"node-{label}"
    node.label = label
    node.state = state
    node.configuration = None
    return node


def _make_mock_link(
    link_id: str = "link-01",
    node_a_label: str = "R1",
    node_b_label: str = "R2",
) -> MagicMock:
    link = MagicMock()
    link.id = link_id
    link.node_a = MagicMock(label=node_a_label)
    link.node_b = MagicMock(label=node_b_label)
    return link


def _make_mock_lab(
    lab_id: str = "lab-abc",
    nodes: list | None = None,
    links: list | None = None,
    converged: bool = True,
) -> MagicMock:
    lab = MagicMock()
    lab.id = lab_id
    lab.is_active.return_value = True
    lab.has_converged.return_value = converged

    _nodes = nodes or [_make_mock_node("R1"), _make_mock_node("R2")]
    _links = links or [_make_mock_link()]

    lab.nodes.return_value = _nodes
    lab.links.return_value = _links
    lab.get_node_by_label.side_effect = lambda name: next(
        (n for n in _nodes if n.label == name), None
    )
    lab.get_link_by_id.side_effect = lambda lid: next(
        (lk for lk in _links if lk.id == lid), None
    )
    return lab


def _make_mock_client(lab: MagicMock | None = None) -> MagicMock:
    client = MagicMock()
    _lab = lab or _make_mock_lab()
    client.import_lab.return_value = _lab
    client.get_local_lab.return_value = _lab
    return client


# ---------------------------------------------------------------------------
# create_lab
# ---------------------------------------------------------------------------


class TestCreateLab:
    _VALID_YAML = "lab:\n  title: test\n  version: '0.1.0'\nnodes: []\nlinks: []"

    def test_returns_lab_id(self, monkeypatch):
        mock_lab = _make_mock_lab(lab_id="lab-xyz")
        mock_client = _make_mock_client(mock_lab)
        mock_client.all_labs.return_value = []  # 同名ラボなし

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import create_lab

            result = create_lab(self._VALID_YAML, title="test-lab")

        assert result == "lab-xyz"
        mock_client.import_lab.assert_called_once()
        # start は呼ばれないこと（start_lab で別途呼ぶ）
        mock_lab.start.assert_not_called()

    def test_deletes_existing_lab_with_same_title(self, monkeypatch):
        """同名ラボが存在する場合、インポート前に削除されること。"""
        existing_active = MagicMock()
        existing_active.title = "agentic-ni-lab"
        existing_active.is_active.return_value = True

        existing_stopped = MagicMock()
        existing_stopped.title = "agentic-ni-lab"
        existing_stopped.is_active.return_value = False

        other_lab = MagicMock()
        other_lab.title = "other-lab"

        new_lab = _make_mock_lab(lab_id="new-lab-id")
        mock_client = MagicMock()
        mock_client.all_labs.return_value = [existing_active, existing_stopped, other_lab]
        mock_client.import_lab.return_value = new_lab

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import create_lab
            result = create_lab(self._VALID_YAML, title="agentic-ni-lab")

        # 同名の2件は stop/remove される
        existing_active.stop.assert_called_once()
        existing_active.remove.assert_called_once()
        existing_stopped.stop.assert_not_called()   # 停止済みなので stop 不要
        existing_stopped.remove.assert_called_once()
        # 別名ラボは触らない
        other_lab.stop.assert_not_called()
        other_lab.remove.assert_not_called()
        assert result == "new-lab-id"

    def test_raises_on_missing_env(self, monkeypatch):
        monkeypatch.delenv("CML_URL", raising=False)
        monkeypatch.delenv("CML_USERNAME", raising=False)
        monkeypatch.delenv("CML_PASSWORD", raising=False)

        # _get_client() が EnvironmentError を送出することを確認
        from agentic_ni.tools import cml_tools

        with pytest.raises(EnvironmentError, match="CML_URL"):
            cml_tools._get_client()


class TestStartLab:
    def test_calls_lab_start(self, monkeypatch):
        mock_lab = _make_mock_lab(lab_id="lab-xyz")
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import start_lab

            start_lab("lab-xyz")

        mock_lab.start.assert_called_once()


# ---------------------------------------------------------------------------
# delete_lab
# ---------------------------------------------------------------------------


class TestDeleteLab:
    def test_stops_and_removes_active_lab(self, monkeypatch):
        mock_lab = _make_mock_lab()
        mock_lab.is_active.return_value = True
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import delete_lab

            delete_lab("lab-abc")

        mock_lab.stop.assert_called_once()
        mock_lab.remove.assert_called_once()

    def test_skips_stop_for_inactive_lab(self, monkeypatch):
        mock_lab = _make_mock_lab()
        mock_lab.is_active.return_value = False
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import delete_lab

            delete_lab("lab-abc")

        mock_lab.stop.assert_not_called()
        mock_lab.remove.assert_called_once()

    def test_raises_when_lab_not_found(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.get_local_lab.return_value = None

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import delete_lab

            with pytest.raises(KeyError, match="lab-missing"):
                delete_lab("lab-missing")


# ---------------------------------------------------------------------------
# push_config
# ---------------------------------------------------------------------------


class TestPushConfig:
    def test_sets_node_configuration(self, monkeypatch):
        r1 = _make_mock_node("R1")
        mock_lab = _make_mock_lab(nodes=[r1])
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import push_config

            push_config("lab-abc", "R1", "hostname R1\n")

        assert r1.configuration == "hostname R1\n"

    def test_raises_when_node_not_found(self, monkeypatch):
        mock_lab = _make_mock_lab(nodes=[_make_mock_node("R1")])
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import push_config

            with pytest.raises(KeyError, match="R99"):
                push_config("lab-abc", "R99", "hostname R99\n")


# ---------------------------------------------------------------------------
# set_link_state
# ---------------------------------------------------------------------------


class TestSetLinkState:
    def test_link_down_sets_100_loss(self, monkeypatch):
        link = _make_mock_link("link-01")
        mock_lab = _make_mock_lab(links=[link])
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import set_link_state

            set_link_state("lab-abc", "link-01", up=False)

        link.set_condition.assert_called_once_with(loss=100.0)
        link.remove_condition.assert_not_called()

    def test_link_up_removes_condition(self, monkeypatch):
        link = _make_mock_link("link-01")
        mock_lab = _make_mock_lab(links=[link])
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import set_link_state

            set_link_state("lab-abc", "link-01", up=True)

        link.remove_condition.assert_called_once()
        link.set_condition.assert_not_called()

    def test_raises_when_link_not_found(self, monkeypatch):
        mock_lab = _make_mock_lab(links=[_make_mock_link("link-01")])
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import set_link_state

            with pytest.raises(KeyError, match="link-99"):
                set_link_state("lab-abc", "link-99", up=False)


# ---------------------------------------------------------------------------
# wait_for_nodes_ready
# ---------------------------------------------------------------------------


class TestWaitForNodesReady:
    def test_returns_true_when_converged_immediately(self, monkeypatch):
        mock_lab = _make_mock_lab(converged=True)
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            with patch("agentic_ni.tools.cml_tools.time.sleep"):
                from agentic_ni.tools.cml_tools import wait_for_nodes_ready

                result = wait_for_nodes_ready("lab-abc", timeout=30)

        assert result is True

    def test_returns_false_on_timeout(self, monkeypatch):
        mock_lab = _make_mock_lab(converged=False)
        mock_client = _make_mock_client(mock_lab)

        # time.monotonic を制御してタイムアウトを即座に再現
        call_count = 0

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            # 初回呼び出し(deadline計算)は0、以降はtimeoutを超えた値を返す
            return 0.0 if call_count == 1 else 999.0

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            with patch("agentic_ni.tools.cml_tools.time.monotonic", side_effect=fake_monotonic):
                with patch("agentic_ni.tools.cml_tools.time.sleep"):
                    from agentic_ni.tools.cml_tools import wait_for_nodes_ready

                    result = wait_for_nodes_ready("lab-abc", timeout=10)

        assert result is False


# ---------------------------------------------------------------------------
# get_lab_nodes / get_lab_links
# ---------------------------------------------------------------------------


class TestGetLabInfo:
    def test_get_lab_nodes_returns_list(self, monkeypatch):
        nodes = [_make_mock_node("R1", "BOOTED"), _make_mock_node("R2", "BOOTED")]
        mock_lab = _make_mock_lab(nodes=nodes)
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import get_lab_nodes

            result = get_lab_nodes("lab-abc")

        assert len(result) == 2
        assert result[0]["label"] == "R1"
        assert result[1]["state"] == "BOOTED"

    def test_get_lab_links_returns_list(self, monkeypatch):
        links = [_make_mock_link("link-01", "R1", "R2")]
        mock_lab = _make_mock_lab(links=links)
        mock_client = _make_mock_client(mock_lab)

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            from agentic_ni.tools.cml_tools import get_lab_links

            result = get_lab_links("lab-abc")

        assert len(result) == 1
        assert result[0] == {"id": "link-01", "node_a": "R1", "node_b": "R2"}

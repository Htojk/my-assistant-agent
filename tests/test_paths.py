"""路径解析：什么东西放哪儿、相对谁算。

这些函数看着琐碎，但"配置里的相对路径按**项目根**算"这条要是反了，同一份配置在不同
cwd 下就会指向不同地方 —— 那不是显示问题，是往错的地方写文件。所以这里既钉形状，
也钉两个兜底：没有项目根时不炸、`MAA_HOME` 是空白当没设。

全程不碰真主目录：`home_dir()` 是专门留的替换点（见它的 docstring），需要时换掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maa import paths


def test_maa_home_默认在用户主目录下(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没人设 `MAA_HOME` 时就落在 `~/.maa`。"""
    monkeypatch.delenv(paths.MAA_HOME_ENV, raising=False)
    monkeypatch.setattr(paths, "home_dir", lambda: tmp_path / "家")

    assert paths.maa_home() == tmp_path / "家" / ".maa"


def test_maa_home_能被环境变量挪走(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试之间互相隔离靠它，用户把数据放别处也靠它。"""
    monkeypatch.setenv(paths.MAA_HOME_ENV, str(tmp_path / "别处"))

    assert paths.maa_home() == tmp_path / "别处"


def test_maa_home_是空白就当没设(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`MAA_HOME=` 或者 `MAA_HOME=" "` 这么写的（比如 shell 里变量没展开）不该变成
    一个叫空的相对目录 —— 那会在 cwd 下偷偷建一个。"""
    monkeypatch.setattr(paths, "home_dir", lambda: tmp_path / "家")
    monkeypatch.setenv(paths.MAA_HOME_ENV, "   ")

    assert paths.maa_home() == tmp_path / "家" / ".maa"


def test_ensure_maa_home_会真的建出来(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """第一次跑（还没 `config init`）时，写配置、开会话库都得先有目录。"""
    monkeypatch.setattr(paths, "home_dir", lambda: tmp_path / "还" / "没" / "有")

    home = paths.ensure_maa_home()

    assert home.is_dir()
    assert home == paths.maa_home()


def test_几个固定位置都挂在主目录下(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.MAA_HOME_ENV, str(tmp_path))

    assert paths.config_path() == tmp_path / "config.toml"
    assert paths.sessions_db_path() == tmp_path / "sessions.db"
    assert paths.user_mcp_path() == tmp_path / "mcp.json"
    assert paths.user_skills_dir() == tmp_path / "skills"


def test_认_git_当项目根(tmp_path: Path) -> None:
    """从很深的子目录里调，也得一路找到根 —— agent 的 cwd 常常不是仓库根。"""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    deep = root / "src" / "深" / "更深"
    deep.mkdir(parents=True)

    assert paths.project_root(deep) == root


def test_没有_git_时认_pyproject(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (root / "sub").mkdir()

    assert paths.project_root(root / "sub") == root


def test_什么都没有就返回出发地(tmp_path: Path) -> None:
    """从仓库外调用不该炸，也不该一路往上摸到别的项目头上。"""
    lonely = tmp_path / "空荡荡"
    lonely.mkdir()

    assert paths.project_root(lonely) == lonely


def test_项目级_mcp_配置在项目根(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    sub = root / "sub"
    sub.mkdir()

    assert paths.project_mcp_path(sub) == root / ".mcp.json"


def test_技能目录从低到高排(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """这个顺序**就是**优先级：同名技能后面的覆盖前面的，排反了会安静地用错技能。"""
    monkeypatch.setenv(paths.MAA_HOME_ENV, str(tmp_path / "home"))
    monkeypatch.setattr(paths, "home_dir", lambda: tmp_path / "home")
    project = tmp_path / "proj"

    found = paths.skill_search_paths(project)

    assert found == [
        tmp_path / "home" / "skills",
        tmp_path / "home" / ".claude" / "skills",
        tmp_path / "home" / ".agents" / "skills",
        project / ".maa" / "skills",
        project / ".agents" / "skills",
    ]
    # 自己加的目录排最后 = 优先级最高，用户能盖掉机器上已有的技能
    assert paths.skill_search_paths(project, extra=["~/mine"])[-1] == Path.home() / "mine"


def test_相对路径按项目根算而不是_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置里写 `out/a.md`，人心里想的是"相对这个项目"。按 cwd 算的话，同一份配置
    从别的目录跑就会写到别的地方去。"""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(tmp_path)

    assert paths.resolve_path("out/a.md", project) == (project / "out" / "a.md").resolve()


def test_绝对路径原样认(tmp_path: Path) -> None:
    """写死了绝对路径就照着走，别自作主张拼到项目根上。"""
    absolute = tmp_path / "a.md"

    assert paths.resolve_path(absolute, tmp_path / "别的") == absolute.resolve()


def test_波浪号展开(tmp_path: Path) -> None:
    assert paths.resolve_path("~/稿子.md", tmp_path) == (Path.home() / "稿子.md").resolve()


def test_resolve_workspace_跟_resolve_path_一条路(tmp_path: Path) -> None:
    """`[backend].workspace = "."` 是最常见的写法，它必须是项目根本身。"""
    assert paths.resolve_workspace(".", tmp_path) == tmp_path.resolve()
    assert paths.resolve_workspace("sub", tmp_path) == (tmp_path / "sub").resolve()


def test_display_能相对就相对(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert paths.display(tmp_path / "out" / "a.md") == str(Path("out") / "a.md")


def test_display_主目录缩成波浪号(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cwd 不在它下面、但在主目录下面时缩成 `~` —— 一长串用户目录名是纯噪音。"""
    elsewhere = tmp_path / "别处"
    elsewhere.mkdir()
    monkeypatch.setattr(paths, "home_dir", lambda: tmp_path / "家")
    monkeypatch.chdir(elsewhere)

    assert paths.display(tmp_path / "家" / "a.md") == "~/a.md"


def test_display_实在缩不了就原样(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """两样都不沾的时候宁可长，也不能给出一个看不懂的短路径。"""
    elsewhere = tmp_path / "别处"
    elsewhere.mkdir()
    monkeypatch.setattr(paths, "home_dir", lambda: tmp_path / "家")
    monkeypatch.chdir(elsewhere)
    faraway = tmp_path / "第三个地方" / "a.md"

    assert paths.display(faraway) == str(faraway.resolve())


def test_display_相对的那条也得是相对_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """先试 cwd，再试主目录 —— 顺序反了的话，主目录下的东西在 cwd 里就显示成 `~/...`，
    而这种路径拿去做别的事（比如当参数传给别人）会指错地方。"""
    monkeypatch.setattr(paths, "home_dir", lambda: tmp_path / "家")
    inside = tmp_path / "家" / "sub"
    inside.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "家")

    assert paths.display(inside / "a.md") == str(Path("sub") / "a.md")

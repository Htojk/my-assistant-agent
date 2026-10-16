"""自检：每一项判得对不对。

这些逻辑原来在 `cli.py` 里，只能靠**子进程**跑 `maa doctor` 间接测 —— 那时"cli.py
覆盖率只有 27%"是个假缺口，命令体跑在另一个进程里，进程内的 coverage 看不见。
搬到 `checks.py` 之后它们在同一个进程里了，那就**没有借口再隔着一层**：子进程那几条
（`test_cli.py`）测的是"命令接上了没有"，这里测的是"每一项判得对不对"。

所以这一节直接调 `checks.run`，看它返回什么、打出来什么。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maa import checks, paths, ui


def _run(workspace: Path, **kwargs: object) -> tuple[bool, str]:
    """跑一次自检，把打出来的表抓下来。返回（全绿没有, 输出）。"""
    with ui.console.capture() as captured:
        passed = checks.run(workspace=str(workspace), **kwargs)
    return passed, captured.get()


def test_都齐了就返回真(maa_home: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")

    passed, said = _run(workspace)

    assert passed, said
    assert "agent 装配" in said
    assert "write_file" in said, "工具表是这一行最有用的一半"
    assert "审批名单" not in said, "默认名单没有错别字，不该出现这行"


def test_缺密钥就返回假(maa_home: Path, workspace: Path) -> None:
    """退出码要靠这个返回值 —— 自检发现问题得能被脚本用上。"""
    passed, said = _run(workspace)

    assert not passed
    assert "密钥没找到" in said


def test_密钥的值绝不打出来(
    maa_home: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自检只说"在不在"。把值印出来等于把密钥写进终端记录、截图、issue 里。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-这只应该出现在环境里")

    passed, said = _run(workspace)

    assert passed
    assert "密钥已就位" in said
    assert "这只应该出现在环境里" not in said


def test_工作目录不存在要报红(
    maa_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")

    passed, said = _run(tmp_path / "没有这个目录")

    assert not passed
    assert "不存在" in said


def test_工作目录不是目录要报红(
    maa_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """给个文件当工作目录 —— 这条能确定性地红，不用去赌 "这个目录不可写"。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    file = tmp_path / "我是文件"
    file.write_text("x", encoding="utf-8")

    passed, said = _run(file)

    assert not passed
    assert "不是目录" in said


def test_工作目录真去写一下(workspace: Path) -> None:
    """问权限位不如直接写 —— Windows 上那一位跟实际能不能写常常对不上。"""
    assert checks._check_workspace(workspace) == (True, f"{paths.display(workspace)} 可读写")

    # 写失败了也不能留垃圾
    assert list(workspace.iterdir()) == []


def test_缺依赖要报红(maa_home: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setattr(checks, "PACKAGES", ("这个包肯定没装",))

    passed, said = _run(workspace)

    assert not passed
    assert "这个包肯定没装" in said
    assert "pip install" in said, "报缺包得顺手给条出路，不然人只能自己猜"


def test_依赖版本超出锁定范围只是提醒(
    maa_home: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提醒（黄）和故障（红）是两回事，这条把分界线钉住。

    deepagents 跨大版本 API 会漂，所以 pyproject 锁了 `<0.8` —— 但装了个 0.9 不代表
    现在就跑不起来，那是"你最好知道"，不是"你不能跑"。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setattr(checks, "_clean_version", lambda _name: (0, 9, 0))

    good, detail = checks._check_dependencies()

    assert good is None, "该是提醒（None），不是故障"
    assert "超出" in detail

    passed, said = _run(workspace)

    assert passed, "提醒不该让退出码变非零"
    assert "!" in said, "提醒要有自己的记号，不然跟红字看着一样"


def test_版本号解析不了就不报警() -> None:
    """自检因为"版本号格式怪"报警，是自检自己的问题，不是环境的问题。"""
    assert checks._clean_version("这个包肯定没装") is None
    assert checks._within(None, (0, 7, 24), (0, 8)) is True


def test_审批名单的错别字是提醒不是故障(
    maa_home: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """名字打错了是"你的门没装好"，不是"这台机器跑不起来" —— 别把人吓去重装 Python。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    maa_home.mkdir(parents=True, exist_ok=True)
    (maa_home / "config.toml").write_text(
        "[models]\n"
        'default = "openai:gpt-5.5"\n'
        "\n"
        "[models.providers.openai]\n"
        'api_key_env = "OPENAI_API_KEY"\n'
        "\n"
        "[approval]\n"
        'gated_tools = ["write_file", "excute"]\n',
        encoding="utf-8",
    )

    passed, said = _run(workspace)

    assert passed, said
    assert "审批名单" in said and "excute" in said


def test_配置读不了也要接着报别的(
    maa_home: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置坏了是最容易让人一头雾水的一项，所以它自己红了就够 —— 后面几项拿不到
    config，跳过是应该的，但**表得打出来**，不能半路抛异常。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    maa_home.mkdir(parents=True, exist_ok=True)
    (maa_home / "config.toml").write_text("这不是 TOML = = =\n", encoding="utf-8")

    passed, said = _run(workspace)

    assert not passed
    assert "配置" in said
    assert "密钥" not in said, "config 都没读出来，后面的项不该硬报（报的每一条都会是假的）"


def test_会话库读不了也要报完整(
    maa_home: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "自检自己不能崩"这条得真验一下：会话库是最容易出岔子的一项（它在主目录里，
    权限、被别的进程占着、路径是个目录，都能让它炸）。

    这里把它指到一个**目录**上 —— sqlite 读不了，异常一路冒到 `_check_sessions`。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setattr(checks.paths, "sessions_db_path", lambda: workspace)

    passed, said = _run(workspace)

    assert not passed
    assert "会话库" in said and "读不了" in said
    assert "agent 装配" in said, "前面红了一项，后面几项照样得跑完 —— 一次报全"


def test_remote_模式是红字不是提醒(
    maa_home: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """remote 只给提示不接通（`backends.REMOTE_HINT`），`build_backend` 直接抛。

    这是红的不是黄的：配了它就跑不起来，让脚本拿到非零退出码才是诚实的。
    ——顺带说明：`record("沙箱后端", cfg.backend.mode != "remote", ...)` 里那个
    判断永远为真（remote 走不到这行就抛了），留着是因为它写着"这里本来想区分点什么"。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    maa_home.mkdir(parents=True, exist_ok=True)
    (maa_home / "config.toml").write_text(
        "[models]\n"
        'default = "openai:gpt-5.5"\n'
        "\n"
        "[models.providers.openai]\n"
        'api_key_env = "OPENAI_API_KEY"\n'
        "\n"
        "[backend]\n"
        'mode = "remote"\n',
        encoding="utf-8",
    )

    passed, said = _run(workspace)

    assert not passed
    assert "沙箱后端" in said
    assert "还没接通" in said


def test_配了端点就把它印出来(
    maa_home: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """密钥那行会带上端点 —— 排错的时候它比密钥本身有用得多。

    实测过一次：端点少了 `/v1` 时网关返回的是网页（HTTP 200），光看状态码看不出来。
    这种时候第一件想确认的事就是"到底在往哪个地址打"。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    maa_home.mkdir(parents=True, exist_ok=True)
    (maa_home / "config.toml").write_text(
        "[models]\n"
        'default = "openai:gpt-5.5"\n'
        "\n"
        "[models.providers.openai]\n"
        'base_url_env = "OPENAI_BASE_URL"\n'
        'api_key_env = "OPENAI_API_KEY"\n'
        "\n"
        "[backend]\n"
        'workspace = "."\n',
        encoding="utf-8",
    )

    _passed, said = _run(workspace)

    assert "端点 http://127.0.0.1:9/v1" in said


def test_没配名字就没有环境变量可找() -> None:
    assert checks._env_value(None) is None
    assert checks._env_value("") is None


def test_全绿和报红的分界在_report() -> None:
    """`run` 的返回值全靠它：warn 放行、bad 不放行。直接钉住。"""
    assert checks._report([("a", "ok", ""), ("b", "warn", "")]) is True
    assert checks._report([("a", "ok", ""), ("b", "bad", "")]) is False
    assert checks._report([]) is True

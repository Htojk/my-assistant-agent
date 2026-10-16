"""CLI 冒烟：每个命令通不通、话说清楚没有。

跑的是 `python -m maa`（`conftest.run_cli`），不是直接调 `cli.main()` —— 入口、
`__main__.py`、退出码、stdout/stderr 分家这些东西，只有真起一个进程才测得到。所以
断言一律看退出码和输出，不看内部状态。

只负责"命令接上了、话说明白了"；每条命令的具体行为在各自的模块测试里。有一条是
例外：`skills list` 会扫开发机真实的 `~/.claude/skills`（`paths.skill_search_paths`
有意算上它，好复用机器上已有的技能），所以那里的断言**不数数量** —— 数了就变成
"取决于谁在跑测试"。
"""

from __future__ import annotations

from pathlib import Path

from conftest import run_cli

from maa import __version__


def test_help_列出所有命令(maa_home: Path) -> None:
    result = run_cli(["--help"], maa_home)

    assert result.returncode == 0
    for command in ("run", "chat", "doctor", "config", "sessions", "skills", "mcp"):
        assert command in result.stdout, f"--help 里没提 {command}"


def test_版本号打得出来(maa_home: Path) -> None:
    """`-V` 也得能用 —— 报问题的时候第一句总是问版本。

    这条是**真的抓到过 bug 的**：`--version` 原本挂在 group 回调里当普通选项，而
    `maa --version` 后面没有命令，Click 先抛 "Missing command" 退 2，那个分支压根
    执行不到。所以要断言退出码，不能只看输出里有没有版本号。
    """
    for flag in ("--version", "-V"):
        result = run_cli([flag], maa_home)

        assert result.returncode == 0, f"{flag} 退出码 {result.returncode}"
        assert __version__ in result.stdout


def test_不给命令就上帮助(maa_home: Path) -> None:
    """光敲 `maa` 不该让人对着空白发呆。"""
    result = run_cli([], maa_home)

    assert result.returncode != 0
    assert "Usage" in result.stdout


def test_doctor_环境齐了就放行(maa_home: Path) -> None:
    result = run_cli(
        ["--model", "openai:gpt-5.5", "doctor"], maa_home, OPENAI_API_KEY="sk-not-a-real-key"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "agent 装配" in result.stdout
    assert "write_todos" in result.stdout


def test_doctor_缺密钥时退出码非零(maa_home: Path) -> None:
    """自检发现问题要能被脚本用上 —— 所以得有退出码，不能只印一行红字。"""
    result = run_cli(["doctor"], maa_home, OPENAI_API_KEY="")

    assert result.returncode == 1
    assert "密钥" in result.stdout


def test_doctor_不碰真主目录也能跑(maa_home: Path) -> None:
    """自检自己得先扛得住"什么都没配" —— 裸机第一次跑就是这状态。"""
    result = run_cli(["--model", "openai:gpt-5.5", "doctor"], maa_home, OPENAI_API_KEY="sk-x")

    assert result.returncode == 0, result.stdout + result.stderr
    # 会话库那一条是 C7 新加的，顺带钉住它还在
    assert "会话" in result.stdout


def test_config_show_没初始化也能看(maa_home: Path) -> None:
    """还没 `config init` 时看到的是内置默认值，不是空白 —— 不然人会以为坏了。"""
    result = run_cli(["config", "show"], maa_home)

    assert result.returncode == 0
    assert "[models]" in result.stdout


def test_config_init_然后_show(maa_home: Path) -> None:
    init = run_cli(["config", "init"], maa_home)
    assert init.returncode == 0
    assert (maa_home / "config.toml").exists()

    show = run_cli(["config", "show"], maa_home)
    assert show.returncode == 0
    assert "[models.providers.openai]" in show.stdout


def test_config_init_不覆盖已有配置(maa_home: Path) -> None:
    run_cli(["config", "init"], maa_home)

    again = run_cli(["config", "init"], maa_home)

    assert again.returncode == 1
    assert "已存在" in again.stdout + again.stderr


def test_config_init_force_才覆盖(maa_home: Path) -> None:
    """想重来的人得有条明路，否则只能自己去猜该删哪个文件。"""
    run_cli(["config", "init"], maa_home)
    (maa_home / "config.toml").write_text('[models]\ndefault = "x:y"\n', encoding="utf-8")

    result = run_cli(["config", "init", "--force"], maa_home)

    assert result.returncode == 0
    assert "x:y" not in (maa_home / "config.toml").read_text(encoding="utf-8")


def test_skills_list_能跑(maa_home: Path) -> None:
    """这条命令刻意不走装配（不需要密钥），所以冒烟能直接跑。"""
    result = run_cli(["skills", "list"], maa_home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "技能源" in result.stdout


def test_mcp_list_说清楚它去哪儿找过(maa_home: Path) -> None:
    """`mcp list` 不连网，随时能跑。但"一个都没读到"和"我不会找"是两回事，所以要么
    列出找过的地方，要么列出真读到的文件 —— 这也是为什么下面那句是个"或"：测试不该
    取决于跑它的机器上有没有 `~/.maa/mcp.json`。"""
    maa_home.mkdir(parents=True, exist_ok=True)
    result = run_cli(["mcp", "list"], maa_home, cwd=maa_home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "找过这些地方" in result.stderr or "读到的文件" in result.stderr
    # 它管不了"连不连得上"，这句话必须说 —— 否则人会以为 list 通过就等于能用了
    assert "maa mcp test" in result.stderr


def test_不认识的命令退出码非零(maa_home: Path) -> None:
    """打错了得有声响，不能悄悄退 0 —— 脚本里那是最难查的一种错。"""
    result = run_cli(["不存在的命令"], maa_home)

    assert result.returncode != 0


def test_doctor_看得见审批名单里的错别字(maa_home: Path) -> None:
    """名单里的名字写错了不会报错、只会静默不生效 —— 自检得替人看见它。

    退出码要照常是 0：错别字是"你的门没装好"，不是"这台机器跑不起来"。
    """
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

    result = run_cli(["doctor"], maa_home, OPENAI_API_KEY="sk-x")

    assert result.returncode == 0, "自检自己不该因为一行错别字就判死刑"
    assert "审批名单" in result.stdout
    assert "excute" in result.stdout


def test_doctor_默认名单不报错(maa_home: Path) -> None:
    """反向：默认配置在**每个** profile 下都得是干净的。

    writer 故意没有 `execute`、planner 连 `delete` 都没有，而默认名单写着它们 ——
    要是这行也报，那它就是条永远亮着的假警报。
    """
    for agent in ("general", "writer", "coder", "planner"):
        result = run_cli(["--agent", agent, "doctor"], maa_home, OPENAI_API_KEY="sk-x")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "审批名单" not in result.stdout, f"{agent} 的默认名单被误报了"

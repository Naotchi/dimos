# インストール
  $ uv run mypy dimos
    Using CPython 3.12.13
    Creating virtual environment at: .venv
    Installed 327 packages in 2.45s
    error: Failed to spawn: `mypy`
      Caused by: No such file or directory (os error 2)

● Bash(uv run --group lint mypy dimos 2>&1 | tail -30)
  ⎿  Installed 25 packages in 192ms
     Success: no issues found in 688 source files
  ⎿  (timeout 10m)

● 通りました。uv run --group lint mypy dimos → Success: no issues found in 688 source files。

  原因: pyproject.toml:413 で default-groups = ["tests"] なので、uv run 単体だと tests グループしか同期されない。mypy は lint グループ
  (pyproject.toml:379) にあるため --group lint が必要。



❯ extraをインストールしなきゃ

● Bash(uv pip install -e '.[misc,sim,visualization,agents,web,perception,unitree,manipulation,cpu]' 2>&1 | tail -40)
  ⎿   + jaxlib==0.10.0
      + jaxopt==0.8.5
      + jupyter-client==8.8.0
     … +37 lines (ctrl+o to expand)
  ⎿  (timeout 10m)

● フル extras インストール完了。

uv sync --extra allの方がいいかも


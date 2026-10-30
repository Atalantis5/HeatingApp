
import os, site, sys

# First, drop system-sites related paths.
original_sys_path = sys.path[:]
known_paths = set()
for path in {'d:\\atalantis虚假的c盘\\w4ai\\代码仓\\05_engine\\04_programfile\\.venv-desktop', 'd:\\atalantis虚假的c盘\\w4ai\\代码仓\\05_engine\\04_programfile\\.venv-desktop\\lib\\site-packages'}:
    site.addsitedir(path, known_paths=known_paths)
system_paths = set(
    os.path.normcase(path)
    for path in sys.path[len(original_sys_path):]
)
original_sys_path = [
    path for path in original_sys_path
    if os.path.normcase(path) not in system_paths
]
sys.path = original_sys_path

# Second, add lib directories.
# ensuring .pth file are processed.
for path in ['D:\\Atalantis虚假的C盘\\W4AI\\代码仓\\05_engine\\04_ProgramFile\\pip-build-env-34furz4w\\overlay\\Lib\\site-packages', 'D:\\Atalantis虚假的C盘\\W4AI\\代码仓\\05_engine\\04_ProgramFile\\pip-build-env-34furz4w\\normal\\Lib\\site-packages']:
    assert not path in sys.path
    site.addsitedir(path)

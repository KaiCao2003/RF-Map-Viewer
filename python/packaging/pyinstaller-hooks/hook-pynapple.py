"""Include Pynapple's dynamically imported core modules in the stable viewer."""

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

hiddenimports = collect_submodules("pynapple.core")
datas = copy_metadata("pynapple")

# Python/Tk Viewer

The Python implementation is a standalone package. Its internal
`rfmapping_viewer.rf_dataset` module validates RF JSON and performs display-time
half-open sums; it deliberately excludes permutation testing and raw-trial
analysis.

## Install and run

```sh
cd ~/Developer/rfmapping_gui/python
~/.virtualenvs/rfmapping/bin/pip install -e '.[test]'
~/.virtualenvs/rfmapping/bin/python rfmapping_gui.py --self-test /path/to/rf.json
~/.virtualenvs/rfmapping/bin/python rfmapping_gui.py /path/to/rf.json
```

The final command needs a Python build with Tk. Headless/Linux validation uses
`--self-test` and pytest.

`examples/probe_position_gui_demo.py` is the standalone probe-position demo
preserved from the remote monolith. It is not imported by the viewer package.

## Validate

```sh
cd ~/Developer/rfmapping_gui/python
PYTHONDONTWRITEBYTECODE=1 ~/.virtualenvs/rfmapping/bin/python -m pytest -q
bash script/tests/test_python_macos_release_scripts.sh
```

## macOS Apple-silicon bundle

```sh
script/build_python_macos_app.sh
script/install_python_macos_app.sh --preflight
script/install_python_macos_app.sh --install
```

Building does not install or launch the app. The installer defaults to a
read-only preflight and requires an explicit state-changing action. Existing
ignored artifacts were retained under `python/dist/`, but releases should be
rebuilt from this source layout.

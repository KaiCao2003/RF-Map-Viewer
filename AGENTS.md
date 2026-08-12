# Agent Instructions

- This repository contains only GUI/viewer implementations. Scientific RF
  analysis and MATLAB work belong in the sibling `../rfmapping` repository.
- Unless the user explicitly requests another target, develop, validate, and
  release only the Python/Tk macOS Apple Silicon implementation under
  `python/`.
- Do not modify or validate Swift, Web, or Windows targets unless the user
  explicitly requests them.
- Run project code only on the remote host reached with `ssh hhw9l84`.
- Use the remote virtual environment at `~/.virtualenvs/rfmapping`.
- Remote source checkouts are expected at `~/Developer/rfmapping_gui`. Do not
  run the project from a local checkout.
- The three implementations must remain independently identifiable:
  `python/`, `swift/`, and `web/`. None may import code through a sibling path
  into `../rfmapping`.
- RF JSON, HD JSON, and probe CSV files are read-only inputs. Treat files under
  `/mnt/senzailab/Kai/#Recording` as authoritative.
- Production Web deployment remains under `/mnt/ssd4.1/Apps/rfmapping`; moving
  the source checkout does not authorize renaming, restarting, or mutating the
  live service.

## Timing and display semantics

- Inspect full `timeBinEdges` and timeline data when investigating apparent
  pre-stimulus responses. With stimuli about 100 ms apart, negative/late bins
  can overlap adjacent stimulus responses.
- Plot range controls only the 2-D RF display. Timeline views retain the full
  time axis unless a dedicated timeline control filters it.

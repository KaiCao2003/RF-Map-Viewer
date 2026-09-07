# Agent Instructions

- This repository contains only GUI/viewer implementations. Scientific RF
  analysis and MATLAB work belong in the sibling `../rfmapping` repository.
- Unless the user explicitly requests another target, develop, validate, and
  release only the Python/Tk macOS Apple Silicon implementation under
  `python/`.
- Do not modify or validate Swift, Web, or Windows targets unless the user
  explicitly requests them.
- Read the untracked `.env.local` for the actual execution host, data root,
  and deployment root. Values in tracked documentation are examples.
- Run project code only on the remote host in `RFMAPPING_REMOTE_HOST`.
- Use the remote virtual environment at `~/.virtualenvs/rfmapping`.
- Remote source checkouts are expected at `~/Developer/rfmapping_gui`. Do not
  run the project from a local checkout.
- The three implementations must remain independently identifiable:
  `python/`, `swift/`, and `web/`. None may import code through a sibling path
  into `../rfmapping`.
- RF JSON, HD JSON, and probe CSV files are read-only inputs. Treat files under
  the locally configured `RFMAPPING_DATA_ROOT` as authoritative.
- Production Web deployment remains under the locally configured deployment root; moving
  the source checkout does not authorize renaming, restarting, or mutating the
  live service.
- Never commit experimental inputs, local configuration, or built packages.
  See `PRIVACY.md` for Git maintenance checks and clone migration guidance.

## Timing and display semantics

- Inspect full `timeBinEdges` and timeline data when investigating apparent
  pre-stimulus responses. With stimuli about 100 ms apart, negative/late bins
  can overlap adjacent stimulus responses.
- Plot range controls only the 2-D RF display. Timeline views retain the full
  time axis unless a dedicated timeline control filters it.

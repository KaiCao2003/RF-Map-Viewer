# Agent Instructions

- Run this project only on the remote host reached with `ssh hhw9l84`.
- Use the remote virtual environment at `~/.virtualenvs/rfmapping`.
- Do not run the project from the local checkout; run commands through SSH, for example:

  ```sh
  ssh hhw9l84 'cd ~/Developer/rfmapping && ~/.virtualenvs/rfmapping/bin/python <script>.py'
  ```

- Ignore MATLAB `.m` files when resolving dependencies or validating the runtime.
- Original MATLAB data-generation sources live on the Linux remote host under
  `/mnt/ssd4.1/Matlab` (not in the local checkout). When investigating JSON
  generation issues, inspect those `.m` files through `ssh hhw9l84`; the
  "ignore `.m` files" rule applies only to dependency resolution and runtime
  validation.

## RFmapping Pipeline Notes

- The active pipeline is:

  ```text
  ~/Developer/sync/matlab.ipynb
  -> /mnt/ssd4.1/Matlab/RFmapping.m
  -> ~/Developer/rfmapping GUI
  ```

- Treat remote raw/session data as source of truth. For timing or JSON-generation
  questions, inspect the relevant files under `/mnt/senzailab/Kai/#Recording`
  and `/mnt/ssd4.1/Matlab`; do not rely on stale JSON copies in the GUI repo.
- When investigating "response before VS", do not only inspect peak or delay
  maps. Check the full `timeBinEdges`, the per-bin timeline, and, when needed,
  recompute from raw `on_list_times.npy`, `trials.mat`, `adc_spike_time.npy`,
  `spike_clusters.npy`, and good-unit labels.
- Important known timing issue: with RFmapping windows such as
  `VSTimeWindow = [-0.1 0.2]` and stimuli spaced about 100 ms apart, negative
  time bins relative to the current stimulus mostly overlap the previous
  stimulus response, and late positive bins can overlap the next stimulus. Those
  spikes are assigned to the current trial's x/y by `RFmapping_core.m`, so
  pre-zero activity in the JSON is not necessarily a plotting bug or true
  response before visual stimulation.
- Plot range should be treated as a 2D RF display control only. Timeline views
  should still show the full time axis and all time-bin maps unless explicitly
  filtered by a dedicated timeline control.

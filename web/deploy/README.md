# RFmapping Web deployment on hhw9l84

The private-network URL is
`http://fsmhhw9l84.fsm.northwestern.edu/rfmapping/` (or the same `/rfmapping/`
path on the host IP). Nginx owns that public route and forwards it to the app's
loopback-only listener at `127.0.0.1:3005`. The include owns only
`/rfmapping/`; the server's existing root `/api/` route remains assigned to
port 8770.

RF datasets and companion files under `/mnt/senzailab` are read-only through
the viewer API. Caches live under `/mnt/ssd4.1/Apps/rfmapping/cache`, and legacy
CSV/image saves live under `/mnt/ssd4.1/Apps/rfmapping/exports`. The figure
composer has a separate, explicit publication boundary,
`RFMAPPING_FIGURE_EXPORT_ROOT` (default `/mnt/senzailab`), so a user can choose
an existing shared destination directory. Figure publication rejects traversal,
dot components and symlinks, permits known extensions only, defaults to no
overwrite, and stages output beside its destination. New outputs use atomic
no-clobber publication. An explicit PNG-directory overwrite first verifies the
exporter manifest, exact directory contents, and every page checksum; it uses a
kernel atomic exchange where available or a journaled, crash-recoverable CIFS
fallback. It never changes a source RF/HD/Probe artifact.

Source browsing and attachment support the current `.rfmap`, `.tc`, and
`.probe` aliases as well as legacy RF/HD `.json` and probe-position `.csv`
files. When both companion names exist in the same discovery location,
`tuning_curves.tc` and `positions.probe` take precedence over their legacy
counterparts.

## Verified host assumptions

- Host: Ubuntu 24.04 on `hhw9l84`; Nginx 1.24 and systemd 255 are active.
- Runtime: `/home/kai/.virtualenvs/rfmapping`; a lingering user systemd manager
  runs the service as `kai`, so releases and rollbacks need no sudo.
- Storage: `/mnt/ssd4.1` had about 3.0 TB free during implementation.
- Privileges: `kai` is in the `sudo` group but does not have passwordless sudo.
  Only the optional Nginx installation/reload needs an interactive
  `ssh -t hhw9l84` session and `sudo -v`. Scripts never prompt for a password.
- Port 3005 is reserved for the loopback-only RFmapping service; ports
  3000-3004 are assigned to existing hhw9l84 tools.

## First install

Run from the remote checkout, never from the local Mac checkout:

```sh
ssh -t hhw9l84
cd ~/Developer/rfmapping_gui/web
./deploy/install.sh --install-deps
systemctl --user enable rfmapping-web.service
./deploy/release.sh --activate
```

`install.sh` creates persistent `releases/`, `cache/`, `exports/`, `tmp/`, and
`shared/` directories. It creates `shared/rfmapping-web.env` only when absent;
on upgrade it migrates a recognized, unmodified legacy network allowlist and
appends missing `RFMAPPING_OUTPUT_ROOT` and `RFMAPPING_FIGURE_EXPORT_ROOT`
defaults. Custom allowlists and other operator settings are preserved.
It also installs and reloads the user service definition without enabling or
starting it. The first release is activated atomically and health-checked
without sudo.

The default `RFMAPPING_ALLOWED_NETWORKS` value permits loopback, non-routable
IPv6 link-local clients (`fe80::/10`, used when macOS resolves the server's
`.local` name), plus `165.124.111.0/24`, `10.103.68.0/24`, and
`172.28.0.0/16`; all other forwarded clients receive 403. The service binds
only `127.0.0.1:3005`, so clients enter through Nginx rather than a public
application port.
The user service reads the dedicated mode-600
`~/.config/lab-access-gate/pi-first-name.env`, which contains only
`MOUSELINE_LOGIN_ANSWER` and the non-secret `MOUSELINE_AUTH_GENERATION` value.
This keeps unrelated mouse-colony credentials out of RFmapping while all lab
apps validate the same PI-first-name answer. RFmapping uses its own
`rfmapping_session` cookie scoped to `/rfmapping`, and stores only session-token
hashes in `~/.local/share/lab-access-gates/rfmapping.sqlite3`. Browser sessions
expire after 30 days. Increment `MOUSELINE_AUTH_GENERATION` whenever the answer
changes; this invalidates all sessions issued under the prior generation.

### Optional clean URL through Nginx

When interactive sudo is available, install the tracked snippet:

```sh
sudo -v
./deploy/install.sh --nginx-file
```

Add this one line inside the existing `server { ... }` block in
`/etc/nginx/conf.d/motive_analysis.conf`:

```nginx
include /etc/nginx/snippets/rfmapping-location.conf;
```

Do not add or replace a root `location /api/`; the RFmapping API is reached only
through `/rfmapping/api/`. Validate and activate the optional proxy:

```sh
sudo nginx -t
sudo systemctl reload nginx
```

The Nginx snippet permits loopback, non-routable IPv6 link-local clients
(`fe80::/10`), plus `165.124.111.0/24`, `10.103.68.0/24`, and
`172.28.0.0/16`, and rejects other clients. The Web app accepts small JSON
control requests, so this location needs no large-request proxy tuning.

## Release and rollback

For each release, sync the approved Web source to
`~/Developer/rfmapping_gui/web` and run:

```sh
ssh -t hhw9l84
cd ~/Developer/rfmapping_gui/web
./deploy/install.sh
./deploy/release.sh --install-deps --activate
```

The release script copies only the self-contained backend, shared figure
renderer, Web frontend, tests, requirements, and deployment files into a new
immutable release. It runs
`npm ci`, the frontend tests, builds the Vite app with the `/rfmapping/` base,
runs a static source-and-bundle gate that rejects the removed menu/window-sync
code and any Probe/HD main-tab definitions, runs the Web backend test gate, and
verifies the backend import before changing `current`. `install.sh` is run on
every upgrade so the active user unit cannot lag the release. Activation
uses an atomic symlink and checks
the loopback health response, an unauthenticated protected-API 401, and the
PI-question login page; failure
automatically restores and restarts a prior gated release. During the first
gated cutover there is no safe rollback target, so a failed activation leaves
the service stopped instead of restoring the legacy ungated release.

Omit `--activate` to build/test/stage without touching the running service. To
roll back to the recorded previous healthy release:

```sh
./deploy/rollback.sh
```

To select a known release, list `releases/` and pass only its basename:

```sh
./deploy/rollback.sh --to 20260803T180000123456789Z-abc123def456
```

Rollback refuses releases that predate the access-gate marker. It also
health-checks its target and restores the original `current` symlink if the
target fails.

## Figure export boundary

The composer exposes PDF and per-page PNG output. A request contains explicit
unit IDs in source order plus a page template whose plots use the stable IDs
`rf.cartesian`, `rf.polar`, `delay.cartesian`, `delay.polar`,
`rgb.cartesian`, `rgb.polar`, `timeline.current`, `hd.line`, `hd.polar`, and
`probe`. Preview and final output use the same renderer. Missing companions are
rendered as explicit placeholders; pages are never silently omitted.

`GET /api/figure-exports/directories` lists directories only. Every final
destination must already exist below `RFMAPPING_FIGURE_EXPORT_ROOT`; path
components may not be symlinks. PDF is one multi-page file. PNG creates one
ordered directory with a manifest recording the source, selected unit index/ID,
page order, render hash and placeholder status. An existing destination returns
HTTP 409 unless the request explicitly enables overwrite. Even then, a PNG
directory is replaceable only if it is a complete, untampered output produced by
this exporter. A raw session directory, symlink, incomplete export, or directory
with unlisted contents is rejected rather than deleted.

## Real-data acceptance checklist

The validation is read-only with respect to source data. It fingerprints all
12 RF, Probe, unsupported legacy-HD, and M15 tuning source files before and
after the run.
API parsing creates only local SSD caches; this acceptance path does not export,
upload, copy, or modify a source artifact.

The only supported tuning source contract is columnar. Its top level is exactly
`metadata`, `angle_bin_edges_deg`, `occupancy_samples`, `occupancy_time_s`,
`unit_id`, `spike_counts`, `firing_rate_hz`, and `unit_data`. Every matrix row
and every `unit_data` column is aligned by the same `unit_id` index.
`unit_data` contains exactly `hd_class`, `rate_mvl`, `spike_angle_mrl`,
`rayleigh_score`, `rayleigh_p`, `rayleigh_significant`, `shuffle_p`, and
`shuffle_significant`. `schema_version`, row-oriented `units`, and the old
`{cluster_id: rates}` mapping are rejected.

1. Before service activation, validate the known artifacts and frontend source
   contract:

   ```sh
   ./deploy/validate_real_data.sh --files-only
   ```

2. After activation, validate direct private access and the three server-side
   remote chooser filters (`rf-json`, `tuning-json`, and `positions-csv`). The
   live API checks all of these real relationships:

   - m17 `260729_2`: RF + 384-channel/620-unit ProbeA; the automatically
     discovered old `{cluster_id: rates}` tuning JSON must be rejected with
     HTTP 422.
   - m17 `260729_4`: 596-unit rotation RF, no Probe positions, and the same
     explicit rejection of the old tuning JSON.
   - m15 `260630_3`: RF + ProbeA + automatic discovery of the same-day
     `260630_1` tuning JSON. The source must expose exactly the eight supported
     top-level fields and all 180 occupancy/count/rate bins; any previous
     `schema_version` + `units` file fails acceptance.
   - m14 `260615_3`: RF + ProbeA with 384 channels and 220 positioned units.

   The m17 binary unit request deliberately supplies a 0..200 ms RF display
   range and still requires the full 500-bin payload, proving that RF display
   range does not truncate Timeline data:

   ```sh
   ./deploy/validate_real_data.sh
   ```

3. Confirm the user service and direct routing:

   ```sh
   systemctl --user status rfmapping-web.service --no-pager
   curl -fsS http://127.0.0.1:3005/rfmapping/api/health
   ```

   The health response must report `1.9.1`.

4. If the optional Nginx include is active, validate it separately:

   ```sh
   ./deploy/validate_real_data.sh \
     --base-url http://127.0.0.1/rfmapping \
     --host-header fsmhhw9l84.fsm.northwestern.edu
   sudo nginx -T | grep -A35 -F 'location ^~ /rfmapping/'
   ```

The fixed acceptance artifacts are:

- Legacy-rejection fixture: m17 `260729_2`, RF shape `[620, 7, 30, 500]`,
  ProbeA 384/620, and the unsupported same-day `260729_1`
  `{cluster_id: rates}` tuning JSON.
- Cross-session legacy-rejection fixture: m17 `260729_4`
  `rotation_30_unitsSpikeCounts_260729_4.json`, 139,143,404 bytes and shape
  `[596, 7, 30, 500]`, with all 500 Timeline bins.
- Columnar migration fixture: m15 `260630_3` RF shape `[146, 7, 30, 300]`,
  ProbeA 384/146, and the same-day `260630_1` `tuning_curves.json` with 147 HD
  units. The validator requires the new eight-field columnar source.
- Probe regression: m14 `260615_3` RF shape `[220, 9, 30, 300]`, paired with
  ProbeA `channels.csv` and `positions.csv`, 384 channels and 220 units.

Inspect failures with
`journalctl --user -u rfmapping-web.service -n 200 --no-pager`.
Changing `RFMAPPING_OUTPUT_ROOT`, `RFMAPPING_FIGURE_EXPORT_ROOT`, or another value in
`shared/rfmapping-web.env` requires
`systemctl --user restart rfmapping-web.service`.

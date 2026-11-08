# Implementation validation: proxy-econ-client

Deferred-gate closure, 2026-11-07: the three environment-bound gates deferred
at implementation time all passed, closing task 4.1's and 3.2's deferred items
and the companion-ref pin.

1. `docker compose config` exit 0 (compose wiring validates; econ block,
   loopback-only ports, read-only credential mounts).
2. `scripts/verify.ps1 -Bun <pinned bun 1.4.4> -HostBinary
   packages/opencode-vibe-oidc/.host/opencode.exe` — **Offline acceptance:
   passed**, run `reports/verification/dfe9d546a3c44182bc7788d8ba5dbf05/`.
   All eleven steps green: 1,395 Python tests passed / 5 documented
   conditional skips, 1,061 provider tests passed, typecheck, both builds,
   both bridges, host smoke + real OpenCode 2.2.5 consumer integration, and
   the four-probe tool baseline. This is the gate's first passing verdict;
   two gate defects (not product defects) had blocked every earlier run:
   - criteria 9.4–9.6 anchored on a provider-doc H1 retitled in `a260963`
     (fixed in `a197240`);
   - the suite-level check turned any skipped case into a step failure, so
     five documented conditional skips failed the python step despite exit 0
     (fixed in `a197240`; requirement 10.8 distinguishes skipped from failed,
     and a skipped evidence test still blocks its criterion — pinned by the
     updated mutation test).
   The bridge-process cleanup bounds were widened for busy hosts in `4e6f8ab`
   (bounded, not instant: 9s drain, 12s forced cleanup); all bridge
   correctness assertions were green before and after.
3. Docker image build with the companion pin `MMHMEM_REF=a421eb8` (contains
   shunt_content and the 2026-11-07 installer bugfixes; pushed to
   norandom/mmhmem main): builder compiled mmhmem 0.5.1 in 2m50s, runtime
   image `mvibe-proxy:local` verified — `mmhmem --version`/`--help` run on
   the python:3.12-slim runtime (builder and runtime both Debian 13 /
   glibc 2.41, so no glibc pin drift), and the image entrypoint is
   `proxy.py` as designed.

Amendment 2 closure, 2026-11-08: the image now embeds the mmhmem CI-built
release binary (Amendment 2) instead of compiling from a git ref, and the
canonical `.dagger/ci.py check` pipeline (shared by local WSL and GitHub
Actions) builds the image, verifies the embedded `mmhmem --version` matches
the pinned `MMHMEM_VERSION`, and verifies the `proxy.py --help` entrypoint.
`MMHMEM_VERSION` is pinned to the published `0.5.2` release (`shunt_content`
included).

Earlier runs remain recorded under `reports/verification/`; the passing
verdict follows the gate fixes above, not removal of failing checks.
